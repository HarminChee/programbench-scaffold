param(
  [Parameter(Mandatory=$true)][string]$Cohort,
  [Parameter(Mandatory=$true)][string]$InitialRoot,
  [Parameter(Mandatory=$true)][string]$OutputBase,
  [string]$SourceRootWsl='/home/programbench/research/oracle-workspace/v3-crosslang-20/sources',
  [string]$TasksRootWsl='/home/programbench/research/programbench/src/programbench/data/tasks',
  [string]$WorkRootWsl='/home/programbench/research/oracle-workspace/v3-crosslang-20/multiround-work',
  [int]$StartRound=1,
  [int]$MaximumRounds=4,
  [int]$LaneCount=4,
  [double]$MinimumCoverage=85.0
)
$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$py='/home/programbench/research/programbench-scaffold/.venv/bin/python'
$selector='/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/tools/select_refinement_cohort_v3.py'
$prepare='/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/tools/prepare_refinement.py'
$combine='/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/tools/prepare_refined_runs.py'
$finalize='/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/tools/run_refined_cohort.py'
$promote='/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/tools/promote_best_refinement_v3.py'
$history=Join-Path $OutputBase 'multiround_history.json'
$prior=$InitialRoot
function To-WslPath([string]$Path){
  $full=[IO.Path]::GetFullPath($Path)
  if($full -notmatch '^([A-Za-z]):\\(.*)$'){throw "Cannot convert path: $full"}
  '/mnt/'+$matches[1].ToLowerInvariant()+'/'+($matches[2]-replace '\\','/')
}
if($StartRound -lt 1 -or $StartRound -gt $MaximumRounds){throw 'StartRound must be between 1 and MaximumRounds'}
for($round=$StartRound;$round -le $MaximumRounds;$round++){
  $roundRoot=Join-Path $OutputBase "round_$round"
  $generation=Join-Path $roundRoot 'generation'
  $final=Join-Path $roundRoot 'final'
  $active=Join-Path $roundRoot 'active_cohort.json'
  $decision=Join-Path $roundRoot 'selection_summary.json'
  & wsl.exe -d ProgramBench-Ubuntu-22.04 -- $py $selector `
    --cohort (To-WslPath $Cohort) --prior-root (To-WslPath $prior) `
    --history (To-WslPath $history) --round $round --minimum-coverage $MinimumCoverage `
    --output-cohort (To-WslPath $active) --output-summary (To-WslPath $decision)
  if($LASTEXITCODE -ne 0){throw 'refinement selection failed'}
  $activeCount=(Get-Content -Raw $decision|ConvertFrom-Json).active
  if($activeCount -eq 0){break}
  & wsl.exe -d ProgramBench-Ubuntu-22.04 -- $py $prepare `
    --cohort (To-WslPath $active) --initial-root (To-WslPath $prior) --refinement-root (To-WslPath $generation)
  if($LASTEXITCODE -ne 0){throw 'refinement preparation failed'}
  $lanes=@()
  for($lane=0;$lane -lt [Math]::Min($LaneCount,$activeCount);$lane++){
    $lanes+=Start-Process powershell.exe -WindowStyle Hidden -PassThru -ArgumentList @(
      '-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'run_refinement_lane_generic.ps1'),
      '-Cohort',$active,'-RunRoot',$generation,'-SourceRootWsl',$SourceRootWsl,
      '-Lane',"$lane",'-LaneCount',"$LaneCount",'-Model','claude-opus-4.8'
    )
  }
  $lanes|Wait-Process
  if($lanes.Where({$_.ExitCode -ne 0}).Count){throw "one or more Opus lanes failed in round $round"}
  & wsl.exe -d ProgramBench-Ubuntu-22.04 -- $py $combine `
    --cohort (To-WslPath $active) --initial-root (To-WslPath $prior) `
    --refinement-root (To-WslPath $generation) --output-root (To-WslPath $final)
  if($LASTEXITCODE -ne 0){throw 'candidate union failed'}
  & wsl.exe -d ProgramBench-Ubuntu-22.04 -- $py $finalize `
    --cohort (To-WslPath $active) --run-root (To-WslPath $final) --tasks-root $TasksRootWsl `
    --work-root "$WorkRootWsl/round_$round" --pytest-python $py
  if($LASTEXITCODE -ne 0){throw "finalization failed in round $round"}
  $promoted=Join-Path $roundRoot 'promoted'
  $promotionSummary=Join-Path $roundRoot 'promotion_summary.json'
  & wsl.exe -d ProgramBench-Ubuntu-22.04 -- $py $promote `
    --prior-root (To-WslPath $prior) --candidate-root (To-WslPath $final) `
    --output-root (To-WslPath $promoted) --output-summary (To-WslPath $promotionSummary)
  if($LASTEXITCODE -ne 0){throw "best-suite promotion failed in round $round"}
  $prior=$promoted
}
@{state='completed';last_root=$prior;history=$history;updated_at=(Get-Date).ToUniversalTime().ToString('o')}|ConvertTo-Json|Set-Content (Join-Path $OutputBase 'multiround_status.json') -Encoding utf8
