$ErrorActionPreference='Stop'
$root=(Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$initial=Join-Path $root 'v3\crosslang20\runs'
$refinement=Join-Path $root 'v3\crosslang20\refinement_round1'
$refined=Join-Path $root 'v3\crosslang20\refined_runs'
$py='/home/programbench/research/programbench-scaffold/.venv/bin/python'
$cohort='/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/configs/cohort.json'
while($true){
  $status=Join-Path $initial 'initial_finalizer_status.json'
  if((Test-Path $status) -and ((Get-Content -Raw $status|ConvertFrom-Json).state -eq 'completed')){break}
  Start-Sleep -Seconds 30
}
while((Get-ChildItem $initial -Directory | Where-Object {Test-Path (Join-Path $_.FullName 'pipeline_summary.json')}).Count -lt 20){
  Start-Sleep -Seconds 30
}
& wsl.exe -d ProgramBench-Ubuntu-22.04 -- $py `
  /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/tools/prepare_refinement.py `
  --cohort $cohort --initial-root /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/runs `
  --refinement-root /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/refinement_round1
$lanes=@()
for($lane=0;$lane -lt 4;$lane++){
  $lanes += Start-Process powershell.exe -WindowStyle Hidden -PassThru `
    -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'run_refinement_lane.ps1'),'-Lane',"$lane",'-LaneCount','4') `
    -RedirectStandardOutput (Join-Path $refinement "lane_$lane.stdout.log") `
    -RedirectStandardError (Join-Path $refinement "lane_$lane.stderr.log")
}
$lanes|Wait-Process
$cohortData=Get-Content -Raw -LiteralPath (Join-Path $root 'v3\crosslang20\configs\cohort.json')|ConvertFrom-Json
$sourceRoot='/home/programbench/research/oracle-workspace/v3-crosslang-20/sources'
foreach($row in $cohortData.instances){
  $candidateDir=Join-Path $refinement "$($row.instance_id)\candidates"
  $rejected=Get-ChildItem $candidateDir -Filter 'batch_*.json' -ErrorAction SilentlyContinue|Where-Object{
    try{(Get-Content -Raw $_.FullName|ConvertFrom-Json).status -eq 'rejected_batch'}catch{$true}
  }
  if($rejected.Count){
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'v3\tools\run_generation_worker_v3.ps1') `
      -InstanceId $row.instance_id -SourceDirWsl "$sourceRoot/$($row.instance_id)" `
      -RunRoot $refinement -Model 'claude-opus-4.8' -MaxTokens 14000 -WorkerId 'refinement_fallback'
  }
}
& wsl.exe -d ProgramBench-Ubuntu-22.04 -- $py `
  /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/tools/prepare_refined_runs.py `
  --cohort $cohort --initial-root /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/runs `
  --refinement-root /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/refinement_round1 `
  --output-root /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/refined_runs
& wsl.exe -d ProgramBench-Ubuntu-22.04 -- $py `
  /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/tools/run_refined_cohort.py `
  --cohort $cohort --run-root /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/refined_runs `
  --tasks-root /home/programbench/research/programbench/src/programbench/data/tasks `
  --work-root /home/programbench/research/oracle-workspace/v3-crosslang-20/refined-work `
  --pytest-python $py
while($true){
  $usable=0
  foreach($row in $cohortData.instances){
    $dir=Join-Path $initial "baselines\$($row.instance_id)"
    if((Test-Path (Join-Path $dir 'native_and_pb_coverage.json')) -or
       ((Get-ChildItem $dir -Recurse -Filter '*.go_coverage_summary.json' -ErrorAction SilentlyContinue).Count -gt 0)){$usable++}
  }
  if($usable -eq 20){break}
  Start-Sleep -Seconds 30
}
& wsl.exe -d ProgramBench-Ubuntu-22.04 -- $py `
  /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/tools/summarize_results.py `
  --cohort $cohort --run-root /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/refined_runs `
  --baseline-root /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/runs/baselines `
  --source-root /home/programbench/research/oracle-workspace/v3-crosslang-20/sources `
  --tasks-root /home/programbench/research/programbench/src/programbench/data/tasks `
  --output /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v3/crosslang20/reports/crosslang20_results.json
@{state='completed';updated_at=(Get-Date).ToUniversalTime().ToString('o')}|ConvertTo-Json|
  Set-Content -LiteralPath (Join-Path $refined 'chain_status.json') -Encoding utf8
