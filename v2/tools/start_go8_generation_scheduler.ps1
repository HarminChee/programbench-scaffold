param(
    [Parameter(Mandatory = $true)][string]$RunRoot,
    [Parameter(Mandatory = $true)][string]$SourceMapPath,
    [int]$MaxConcurrentRepos = 2,
    [string]$Model = 'claude-sonnet-5',
    [int]$MaxTokens = 12000
)

$ErrorActionPreference = 'Stop'
$scheduler = Join-Path $PSScriptRoot 'run_go8_generation_scheduler.ps1'
$logDir = Join-Path $RunRoot 'scheduler_logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$stdout = Join-Path $logDir "go8_scheduler_$stamp.stdout.log"
$stderr = Join-Path $logDir "go8_scheduler_$stamp.stderr.log"
$arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$scheduler,'-RunRoot',$RunRoot,'-SourceMapPath',$SourceMapPath,'-MaxConcurrentRepos',$MaxConcurrentRepos,'-Model',$Model,'-MaxTokens',$MaxTokens)

# Normalize PATH/Path before spawning, matching the per-repository launcher.
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
Remove-Item Env:PATH -ErrorAction SilentlyContinue
$env:Path = @($machinePath, $userPath) -ne '' -join ';'
$env:APPDATA = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
$env:LOCALAPPDATA = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$env:USERPROFILE = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
$process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
[ordered]@{ pid=$process.Id; started_at=(Get-Date).ToUniversalTime().ToString('o'); stdout_log=$stdout; stderr_log=$stderr } | ConvertTo-Json -Compress
