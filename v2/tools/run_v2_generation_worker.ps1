param(
    [Parameter(Mandatory = $true)][string]$InstanceId,
    [Parameter(Mandatory = $true)][string]$SourceDirWsl,
    [Parameter(Mandatory = $true)][string]$RunRoot,
    [string]$Model = 'claude-sonnet-5',
    [int]$MaxTokens = 6000,
    [int]$BatchModulo = 1,
    [int]$BatchRemainder = 0,
    [string]$WorkerId = 'main'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pythonPlan = "$repoRoot\v2\tools\build_agent_context_v2.py"
$materialize = "$repoRoot\v2\tools\materialize_agent_cases_v2.py"
$invoke = "$repoRoot\v2\tools\invoke_v2_agent_batch.ps1"
$runDir = Join-Path $RunRoot $InstanceId
$statusPath = Join-Path $runDir "generation_worker_status_$WorkerId.json"

function To-WslPath([string]$WindowsPath) {
    # Output files do not exist yet when we hand their paths to a Linux tool.
    # GetFullPath preserves those paths while still normalizing relative input.
    $resolved = [System.IO.Path]::GetFullPath($WindowsPath)
    if ($resolved -notmatch '^([A-Za-z]):\\(.*)$') { throw "Cannot convert path to WSL: $resolved" }
    return '/mnt/' + $matches[1].ToLowerInvariant() + '/' + ($matches[2] -replace '\\','/')
}
function Write-Status([string]$State, [string]$Batch, [string]$Detail) {
    $payload = [ordered]@{ instance_id=$InstanceId; state=$State; batch=$Batch; detail=$Detail; updated_at=(Get-Date).ToUniversalTime().ToString('o'); model=$Model }
    $payload | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding utf8
    $payload | ConvertTo-Json -Compress
}

$secretPath = Join-Path $env:APPDATA 'AgentMaestro\api-key.dpapi'
$encrypted = (Get-Content -Raw -LiteralPath $secretPath).Trim()
$secure = ConvertTo-SecureString -String $encrypted
$credential = [pscredential]::new('AgentMaestro', $secure)
$env:AGENT_MAESTRO_API_KEY = $credential.GetNetworkCredential().Password
if ($env:AGENT_MAESTRO_API_KEY -notmatch '^[0-9a-f]{64}$') { throw 'Invalid Maestro key format' }
$credential = $null; $secure = $null; $encrypted = $null

try {
    $manifest = Get-Content -Raw -LiteralPath (Join-Path $runDir 'topic_batches\batch_manifest.json') | ConvertFrom-Json
    for ($batchIndex = 0; $batchIndex -lt $manifest.batches.Count; $batchIndex++) {
        if (($batchIndex % $BatchModulo) -ne $BatchRemainder) { continue }
        $batch = $manifest.batches[$batchIndex]
        $batchName = [string]$batch.batch_id
        $batchDir = Join-Path $runDir "topic_batches\$batchName"
        $contextDir = Join-Path $runDir "agent_context\$batchName"
        $outputDir = Join-Path $runDir 'agent_output'
        $outputFile = Join-Path $outputDir "$batchName.txt"
        $candidateDir = Join-Path $runDir 'candidates'
        $candidateFile = Join-Path $candidateDir "$batchName.json"
        if (Test-Path -LiteralPath $candidateFile) { Write-Status 'skipped_completed' $batchName 'candidate manifest already exists'; continue }
        try {
            New-Item -ItemType Directory -Force -Path $contextDir,$outputDir,$candidateDir | Out-Null
            Write-Status 'building_context' $batchName 'source-only context'
            & wsl.exe -d ProgramBench-Ubuntu-22.04 -- python3 (To-WslPath $pythonPlan) --plan-dir (To-WslPath (Join-Path $runDir 'plan')) --batch-dir (To-WslPath $batchDir) --source-dir $SourceDirWsl --output-dir (To-WslPath $contextDir)
            if ($LASTEXITCODE -ne 0) { throw "context failed: $batchName" }
            Write-Status 'calling_model' $batchName 'bounded topic generation'
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $invoke -BatchPrompt (Join-Path $batchDir 'agent_prompt.md') -SourceExcerpt (Join-Path $contextDir 'source_excerpt.md') -OutputPath $outputFile -MaxTokens $MaxTokens -Model $Model
            if ($LASTEXITCODE -ne 0) { throw "agent failed: $batchName" }
            Write-Status 'validating_candidates' $batchName 'matrix validation'
            & wsl.exe -d ProgramBench-Ubuntu-22.04 -- python3 (To-WslPath $materialize) --agent-output (To-WslPath $outputFile) --matrix-rows (To-WslPath (Join-Path $batchDir 'matrix_rows.json')) --output (To-WslPath $candidateFile)
            if ($LASTEXITCODE -ne 0) { throw "materialize failed: $batchName" }
            Write-Status 'completed_batch' $batchName 'candidate manifest written'
        } catch {
            # A malformed model response must not discard all other topic
            # batches.  Keep an explicit zero-case audit record, then advance.
            $rejected = [ordered]@{
                profile = 'programbench_oracle_gym_v2'
                source_policy = 'target PB oracle forbidden'
                candidate_case_count = 0
                cases = @()
                errors = @($_.Exception.Message)
                status = 'rejected_batch'
                batch_id = $batchName
            }
            $rejected | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $candidateFile -Encoding utf8
            Write-Status 'rejected_batch' $batchName $_.Exception.Message
            continue
        }
    }
    Write-Status 'completed' '' 'all topic batches have candidate manifests'
} catch {
    Write-Status 'failed' '' $_.Exception.Message
    throw
} finally {
    Remove-Item Env:AGENT_MAESTRO_API_KEY -ErrorAction SilentlyContinue
}
