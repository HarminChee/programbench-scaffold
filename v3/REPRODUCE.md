# Reproducing V3

## Prerequisites

V3 uses the same Windows/WSL, ProgramBench task, three-binary, Docker, Go,
pytest, Agent Maestro, and secret-handling prerequisites as V2. The target
repository's PB official oracle tests remain strictly held out.

The examples below assume:

```bash
ROOT=/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold
INSTANCE=sclevine__yj.8016400
SOURCE=/path/to/pinned/source
RUN=$ROOT/v3/runs/reproduction
TASKS=/home/programbench/research/programbench/tasks
WORK=/home/programbench/research/oracle-workspace/v3-reproduction
```

## 1. Build and validate the V3 plan

```bash
python3 "$ROOT/v3/tools/build_plan_v3.py" "$INSTANCE" \
  --source-dir "$SOURCE" \
  --output-dir "$RUN/$INSTANCE/plan"

python3 "$ROOT/v3/tools/validate_plan_v3.py" \
  --plan-dir "$RUN/$INSTANCE/plan"
```

## 2. Build multi-perspective batches

```bash
python3 "$ROOT/v3/tools/build_batches_v3.py" \
  --plan-dir "$RUN/$INSTANCE/plan" \
  --output-dir "$RUN/$INSTANCE/batches" \
  --minimum-cases 40
```

For a later PB-free refinement round, use a generic perspective subset only
after `build_refinement_feedback_v3.py` has written self-measured feedback:

```bash
python3 "$ROOT/v3/tools/build_batches_v3.py" \
  --plan-dir "$RUN/$INSTANCE/plan" \
  --output-dir "$RUN/$INSTANCE/batches_refinement" \
  --perspectives source_corpus_harvest,protocol_and_fixture_matrix
```

## 3. Generate candidates through Agent Maestro

From Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "C:\Users\v-haominqi\Documents\Codex\pb-scaffold\v3\tools\start_generation_worker_v3.ps1" `
  -InstanceId "sclevine__yj.8016400" `
  -SourceDirWsl "/path/to/pinned/source" `
  -RunRoot "C:\Users\v-haominqi\Documents\Codex\pb-scaffold\v3\runs\reproduction" `
  -Model "claude-sonnet-5"
```

The worker creates bounded source/docs/native-test context, calls the model,
normalizes the portable fixture DSL, and persists restartable candidates.

## 4. Capture, filter, validate, and measure

```bash
python3 "$ROOT/v3/tools/run_go_v3_final.py" "$INSTANCE" \
  --run-root "$RUN" \
  --tasks-root "$TASKS" \
  --work-root "$WORK" \
  --xdist 4 \
  --case-timeout 20 \
  --behavior-cap 0
```

The finalizer captures reference behavior, creates pytest oracles, removes
empty/return-code-only and strict-exact duplicate cases, runs all quality
gates, compares the three binaries, and measures Go coverage.

## 5. Refine from internal evidence

```bash
python3 "$ROOT/v3/tools/build_refinement_feedback_v3.py" --help
python3 "$ROOT/v3/tools/evaluate_stop_v3.py" --help
```

Use only the generated suite's own behavior clusters, reachability evidence,
coverage gaps, and fixture failures. PB official tests remain unavailable to
the agent and refinement logic.

## 6. Optional dynamic diagnostics

```bash
python3 "$ROOT/v3/tools/run_dynamic_path_metrics_v3.py" --help
python3 "$ROOT/v3/tools/run_qemu_timing_cohort_v3.py" --help
```

QEMU/Callgrind/AFL-like results are dynamic novelty diagnostics. They are not
formal percentages of all possible paths and do not replace Go statement or
executable-line coverage.

## 7. Publish and verify

```bash
python3 "$ROOT/v3/tools/build_release_report_v3.py"
python3 "$ROOT/tools/package_oracle_gym_release.py" --version v3
sha256sum "$ROOT"/v3/published_tests/*/oracle_tests.tar.gz \
  "$ROOT"/v3/published_tests/chroma/*/oracle_tests.tar.gz
```

Compare those hashes with [`PUBLISHED_ARTIFACTS.json`](PUBLISHED_ARTIFACTS.json).
