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

$ErrorActionPreference='Stop'
$worker=Join-Path $PSScriptRoot 'run_generation_worker_v3.ps1'
$logs=Join-Path $RunRoot "$InstanceId\worker_logs"
New-Item -ItemType Directory -Force -Path $logs|Out-Null
$stamp=Get-Date -Format 'yyyyMMdd_HHmmss'
$stdout=Join-Path $logs "$WorkerId.$stamp.stdout.log"
$stderr=Join-Path $logs "$WorkerId.$stamp.stderr.log"
$machinePath=[Environment]::GetEnvironmentVariable('Path','Machine')
$userPath=[Environment]::GetEnvironmentVariable('Path','User')
Remove-Item Env:PATH -ErrorAction SilentlyContinue
$env:Path=@($machinePath,$userPath)-ne '' -join ';'
$args=@(
    '-NoProfile','-ExecutionPolicy','Bypass','-File',$worker,
    '-InstanceId',$InstanceId,'-SourceDirWsl',$SourceDirWsl,'-RunRoot',$RunRoot,
    '-Model',$Model,'-MaxTokens',$MaxTokens,'-BatchModulo',$BatchModulo,
    '-BatchRemainder',$BatchRemainder,'-WorkerId',$WorkerId
)
$process=Start-Process powershell.exe -ArgumentList $args -WindowStyle Hidden `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
@{instance_id=$InstanceId;worker_id=$WorkerId;pid=$process.Id;stdout=$stdout;stderr=$stderr}|
    ConvertTo-Json -Compress
