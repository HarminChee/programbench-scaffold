param(
    [Parameter(Mandatory = $true)][string]$InstanceId,
    [Parameter(Mandatory = $true)][string]$SourceDirWsl,
    [Parameter(Mandatory = $true)][string]$RunRoot,
    [string]$Model = 'claude-sonnet-5',
    [int]$MaxTokens = 12000,
    [int]$BatchModulo = 1,
    [int]$BatchRemainder = 0,
    [string]$WorkerId = 'main'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$runDir = Join-Path $RunRoot $InstanceId
$statusPath = Join-Path $runDir "generation_worker_status_$WorkerId.json"

function To-WslPath([string]$WindowsPath) {
    $resolved = [IO.Path]::GetFullPath($WindowsPath)
    if ($resolved -notmatch '^([A-Za-z]):\\(.*)$') { throw "Cannot convert path: $resolved" }
    '/mnt/' + $matches[1].ToLowerInvariant() + '/' + ($matches[2] -replace '\\','/')
}
function Write-Status([string]$State,[string]$Batch,[string]$Detail) {
    $value=[ordered]@{
        instance_id=$InstanceId; worker_id=$WorkerId; state=$State; batch=$Batch;
        detail=$Detail; model=$Model; updated_at=(Get-Date).ToUniversalTime().ToString('o')
    }
    $value | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding utf8
}

$secretPath = Join-Path $env:APPDATA 'AgentMaestro\api-key.dpapi'
$encrypted = (Get-Content -Raw -LiteralPath $secretPath).Trim()
$secure = ConvertTo-SecureString -String $encrypted
$credential = [pscredential]::new('AgentMaestro', $secure)
$env:AGENT_MAESTRO_API_KEY = $credential.GetNetworkCredential().Password
if ($env:AGENT_MAESTRO_API_KEY -notmatch '^[0-9a-f]{64}$') { throw 'Invalid Maestro key format' }
$credential=$null; $secure=$null; $encrypted=$null

try {
    $manifest=Get-Content -Raw -LiteralPath (Join-Path $runDir 'batches\batch_manifest.json')|ConvertFrom-Json
    for($i=0;$i -lt $manifest.batches.Count;$i++){
        if(($i % $BatchModulo) -ne $BatchRemainder){continue}
        $batch=[string]$manifest.batches[$i].batch_id
        $batchDir=Join-Path $runDir "batches\$batch"
        $contextDir=Join-Path $runDir "contexts\$batch"
        $output=Join-Path $runDir "agent_outputs\$batch.txt"
        $candidate=Join-Path $runDir "candidates\$batch.json"
        if(Test-Path -LiteralPath $candidate){
            $existing = Get-Content -Raw -LiteralPath $candidate | ConvertFrom-Json
            if([string]$existing.status -ne 'rejected_batch'){
                Write-Status 'skipped_completed' $batch 'candidate exists'
                continue
            }
            Remove-Item -LiteralPath $candidate -Force
            Write-Status 'retrying_rejected' $batch 'prior model response or materialization produced no valid cases'
        }
        try {
            New-Item -ItemType Directory -Force -Path $contextDir,(Split-Path $output),(Split-Path $candidate)|Out-Null
            Write-Status 'building_context' $batch 'source/docs/native tests'
            & wsl.exe -d ProgramBench-Ubuntu-22.04 -- python3 `
                (To-WslPath (Join-Path $repoRoot 'v3\tools\build_context_v3.py')) `
                --plan-dir (To-WslPath (Join-Path $runDir 'plan')) `
                --batch-dir (To-WslPath $batchDir) --source-dir $SourceDirWsl `
                --output-dir (To-WslPath $contextDir)
            if($LASTEXITCODE -ne 0){throw 'context build failed'}
            Write-Status 'calling_model' $batch 'Agent Maestro'
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
                (Join-Path $repoRoot 'v3\tools\invoke_agent_v3.ps1') `
                -BatchPrompt (Join-Path $batchDir 'agent_prompt.md') `
                -SourceContext (Join-Path $contextDir 'source_context.md') `
                -OutputPath $output -MaxTokens $MaxTokens -Model $Model
            if($LASTEXITCODE -ne 0){throw 'agent call failed'}
            Write-Status 'materializing' $batch 'validate flat fixture DSL'
            & wsl.exe -d ProgramBench-Ubuntu-22.04 -- python3 `
                (To-WslPath (Join-Path $repoRoot 'v3\tools\materialize_candidates_v3.py')) `
                --agent-output (To-WslPath $output) `
                --matrix-rows (To-WslPath (Join-Path $batchDir 'matrix_rows.json')) `
                --output (To-WslPath $candidate)
            if($LASTEXITCODE -ne 0){throw 'candidate materialization failed'}
            Write-Status 'completed_batch' $batch 'candidate written'
        } catch {
            @{
                profile='programbench_oracle_gym_v3'; cases=@(); candidate_case_count=0;
                status='rejected_batch'; batch_id=$batch; errors=@($_.Exception.Message)
            } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $candidate -Encoding utf8
            Write-Status 'rejected_batch' $batch $_.Exception.Message
        }
    }
    Write-Status 'completed' '' 'all assigned batches completed'
} finally {
    Remove-Item Env:AGENT_MAESTRO_API_KEY -ErrorAction SilentlyContinue
}
