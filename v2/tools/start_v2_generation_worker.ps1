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
$worker = Join-Path $PSScriptRoot 'run_v2_generation_worker.ps1'
$runDir = Join-Path $RunRoot $InstanceId
$logDir = Join-Path $runDir 'worker_logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$stdout = Join-Path $logDir "generation_${WorkerId}_$stamp.stdout.log"
$stderr = Join-Path $logDir "generation_${WorkerId}_$stamp.stderr.log"
$arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$worker,'-InstanceId',$InstanceId,'-SourceDirWsl',$SourceDirWsl,'-RunRoot',$RunRoot,'-Model',$Model,'-MaxTokens',$MaxTokens,'-BatchModulo',$BatchModulo,'-BatchRemainder',$BatchRemainder,'-WorkerId',$WorkerId)
# The desktop host can expose both PATH and Path.  Normalize the starter's own
# environment before Start-Process serializes it; the worker gets its DPAPI
# credential itself and receives no inherited API secret.
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
Remove-Item Env:PATH -ErrorAction SilentlyContinue
$env:Path = @($machinePath, $userPath) -ne '' -join ';'
$env:APPDATA = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
$env:LOCALAPPDATA = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$env:USERPROFILE = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
$process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
[ordered]@{ instance_id=$InstanceId; pid=$process.Id; started_at=(Get-Date).ToUniversalTime().ToString('o'); stdout_log=$stdout; stderr_log=$stderr } | ConvertTo-Json -Compress
