param(
    [Parameter(Mandatory = $true)][string]$RunRoot,
    [Parameter(Mandatory = $true)][string]$SourceMapPath,
    [int]$MaxConcurrentRepos = 2,
    [string]$Model = 'claude-sonnet-5',
    [int]$MaxTokens = 12000
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$starter = Join-Path $PSScriptRoot 'start_v2_generation_worker.ps1'
$statusPath = Join-Path $RunRoot 'go8_generation_scheduler_status.json'
$map = Get-Content -Raw -LiteralPath $SourceMapPath | ConvertFrom-Json
$ids = @($map.psobject.Properties.Name)

function Get-RepoState([string]$InstanceId) {
    $dir = Join-Path $RunRoot $InstanceId
    $files = @(Get-ChildItem -LiteralPath $dir -Filter 'generation_worker_status_shard*.json' -ErrorAction SilentlyContinue)
    if ($files.Count -lt 3) { return 'not_started' }
    $states = @($files | ForEach-Object { (Get-Content -Raw -LiteralPath $_.FullName | ConvertFrom-Json).state })
    if ($states -contains 'failed') { return 'failed' }
    if ($states -contains 'calling_model' -or $states -contains 'building_context' -or $states -contains 'validating_candidates') { return 'running' }
    if (($states | Where-Object { $_ -eq 'completed' }).Count -eq 3) { return 'completed' }
    return 'running'
}

function Write-SchedulerStatus {
    $states = [ordered]@{}
    foreach ($id in $ids) { $states[$id] = Get-RepoState $id }
    [ordered]@{ updated_at=(Get-Date).ToUniversalTime().ToString('o'); max_concurrent_repos=$MaxConcurrentRepos; repositories=$states } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $statusPath -Encoding utf8
    # Do not let PowerShell enumerate the ordered dictionary into individual
    # DictionaryEntry objects; callers need its Values and keyed lookup.
    Write-Output -NoEnumerate $states
}

while ($true) {
    $states = Write-SchedulerStatus
    $runningCount = @($states.Values | Where-Object { $_ -eq 'running' }).Count
    $pending = @($ids | Where-Object { $states[$_] -eq 'not_started' })
    while ($runningCount -lt $MaxConcurrentRepos -and $pending.Count -gt 0) {
        $id = $pending[0]
        $src = [string]$map.$id
        0..2 | ForEach-Object {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $starter -InstanceId $id -SourceDirWsl $src -RunRoot $RunRoot -Model $Model -MaxTokens $MaxTokens -BatchModulo 3 -BatchRemainder $_ -WorkerId "shard$_" | Out-Null
        }
        $runningCount++
        $pending = @($pending | Select-Object -Skip 1)
    }
    $states = Write-SchedulerStatus
    if (@($states.Values | Where-Object { $_ -eq 'not_started' -or $_ -eq 'running' }).Count -eq 0) { break }
    Start-Sleep -Seconds 20
}
