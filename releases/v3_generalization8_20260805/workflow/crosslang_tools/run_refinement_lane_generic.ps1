param(
  [Parameter(Mandatory=$true)][string]$Cohort,
  [Parameter(Mandatory=$true)][string]$RunRoot,
  [Parameter(Mandatory=$true)][string]$SourceRootWsl,
  [Parameter(Mandatory=$true)][int]$Lane,
  [int]$LaneCount=4,
  [string]$Model='claude-opus-4.8'
)
$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$rows=(Get-Content -Raw -LiteralPath $Cohort|ConvertFrom-Json).instances
for($index=0;$index -lt $rows.Count;$index++){
  if(($index % $LaneCount) -ne $Lane){continue}
  $row=$rows[$index]
  $batchRoot=Join-Path (Join-Path $RunRoot $row.instance_id) 'batches'
  if(-not (Test-Path -LiteralPath $batchRoot)){
    Write-Warning "Skipping $($row.instance_id): refinement preparation produced no batches"
    continue
  }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'v3\tools\run_generation_worker_v3.ps1') `
    -InstanceId $row.instance_id -SourceDirWsl "$SourceRootWsl/$($row.instance_id)" `
    -RunRoot $RunRoot -Model $Model -MaxTokens 16000 -WorkerId "multiround_lane_$Lane"
  if($LASTEXITCODE -ne 0){throw "refinement generation failed for $($row.instance_id)"}
}
