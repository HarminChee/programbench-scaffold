param(
    [Parameter(Mandatory = $true)][string]$InstanceIds,
    [Parameter(Mandatory = $true)][string]$RunRoot,
    [Parameter(Mandatory = $true)][string]$WorkRootWsl,
    [string]$Model = 'claude-sonnet-5',
    [int]$MaxTokens = 12000,
    [string]$ShardId = 'main'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$sources = Get-Content -Raw -LiteralPath (
    Join-Path $repoRoot 'v3\configs\go10_source_paths_wsl.json'
) | ConvertFrom-Json
$statusPath = Join-Path $RunRoot "pipeline_scheduler_status_$ShardId.json"

function To-WslPath([string]$WindowsPath) {
    $resolved = [IO.Path]::GetFullPath($WindowsPath)
    if ($resolved -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "Cannot convert path: $resolved"
    }
    '/mnt/' + $matches[1].ToLowerInvariant() + '/' + ($matches[2] -replace '\\','/')
}

function Write-Status([string]$Instance, [string]$State, [string]$Detail) {
    [ordered]@{
        shard_id = $ShardId
        instance_id = $Instance
        state = $State
        detail = $Detail
        updated_at = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding utf8
}

$ids = @($InstanceIds.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$runRootWsl = To-WslPath $RunRoot
foreach ($id in $ids) {
    $repoDir = Join-Path $RunRoot $id
    $merged = Join-Path $repoDir 'merged\v3_candidates.json'
    New-Item -ItemType Directory -Force -Path (Split-Path $merged) | Out-Null
    if (-not (Test-Path -LiteralPath $merged)) {
        Write-Status $id 'generation' 'source-only agent batches'
        $source = [string]$sources.$id
        if (-not $source) { throw "Missing source mapping for $id" }
        & (Join-Path $PSScriptRoot 'run_generation_worker_v3.ps1') `
            -InstanceId $id -SourceDirWsl $source -RunRoot $RunRoot `
            -Model $Model -MaxTokens $MaxTokens -WorkerId $ShardId
        if ($LASTEXITCODE -ne 0) { throw "Generation failed for $id" }
        Write-Status $id 'combine' 'combining valid candidate batches'
        & wsl.exe -d ProgramBench-Ubuntu-22.04 -- python3 `
            /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/tools/combine_candidates_v3.py `
            --candidate-dir (To-WslPath (Join-Path $repoDir 'candidates')) `
            --output (To-WslPath $merged)
        if ($LASTEXITCODE -ne 0) { throw "Candidate combine failed for $id" }
    }
    Write-Status $id 'finalize' 'capture, quality filter, three-binary coverage'
    & wsl.exe -d ProgramBench-Ubuntu-22.04 -- python3 `
        /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/tools/run_go_v3_final.py `
        $id --run-root $runRootWsl `
        --tasks-root /home/programbench/research/programbench/src/programbench/data/tasks `
        --work-root $WorkRootWsl --xdist 4 --case-timeout 10 --resume
    if ($LASTEXITCODE -ne 0) { throw "Finalization failed for $id" }
    Write-Status $id 'completed' 'final suite and coverage complete'
}
Write-Status '' 'completed_all' "$($ids.Count) repositories complete"
