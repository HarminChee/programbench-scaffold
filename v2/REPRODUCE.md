# Reproducing V2

## Prerequisites

- Windows PowerShell plus the `ProgramBench-Ubuntu-22.04` WSL distribution.
- Python 3.10+, Go, Docker, pytest, and the ProgramBench task material.
- A pinned target source checkout.
- A cleanroom reference executable, a source-built executable, and a
  coverage-built executable available through the task layout expected by the
  shared harness.
- Agent Maestro on `127.0.0.1:23333` with the existing
  `AGENT_MAESTRO_API_KEY`. Never commit or print that key.

The target repository's PB official oracle tests must not appear in the source
checkout, plan, prompt, context, candidate manifests, or reference-capture
workspace.

The examples below assume:

```bash
ROOT=/mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold
INSTANCE=sclevine__yj.8016400
SOURCE=/path/to/pinned/source
RUN=$ROOT/v2/runs/reproduction
TASKS=/home/programbench/research/programbench/tasks
WORK=/home/programbench/research/oracle-workspace/v2-reproduction
```

## 1. Build and validate the behavior plan

```bash
python3 "$ROOT/v2/tools/build_behavior_plan_v2.py" "$INSTANCE" \
  --source-dir "$SOURCE" \
  --output-dir "$RUN/$INSTANCE/plan"

python3 "$ROOT/v2/tools/validate_behavior_plan_v2.py" \
  --plan-dir "$RUN/$INSTANCE/plan"
```

## 2. Build expansive generation batches

```bash
python3 "$ROOT/v2/tools/build_topic_batches_v2.py" \
  --plan-dir "$RUN/$INSTANCE/plan" \
  --output-dir "$RUN/$INSTANCE/topic_batches" \
  --expansive \
  --minimum-cases 40
```

## 3. Generate candidates through Agent Maestro

From Windows PowerShell, use the same physical `RUN` directory in Windows path
form:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "C:\Users\v-haominqi\Documents\Codex\pb-scaffold\v2\tools\start_v2_generation_worker.ps1" `
  -InstanceId "sclevine__yj.8016400" `
  -SourceDirWsl "/path/to/pinned/source" `
  -RunRoot "C:\Users\v-haominqi\Documents\Codex\pb-scaffold\v2\runs\reproduction" `
  -Model "claude-sonnet-5"
```

The worker creates bounded source-only context, calls the model, validates its
JSON, and writes one candidate manifest per batch. It is restartable and skips
completed batches.

## 4. Capture gold behavior, filter, and measure coverage

```bash
python3 "$ROOT/v2/tools/run_go_v2_final_manual.py" "$INSTANCE" \
  --run-root "$RUN" \
  --tasks-root "$TASKS" \
  --work-root "$WORK" \
  --xdist 4 \
  --case-timeout 20
```

This stage captures behavior from the cleanroom reference executable, builds
the pytest oracle bundle, rejects weak or non-deterministic cases, checks dummy
implementations, compares the three binaries, and measures Go source coverage.

## 5. Produce the cohort report

```bash
python3 "$ROOT/v2/tools/report_go10_v2_results.py" \
  --run-root "$RUN" \
  --cohort "$ROOT/v2/configs/go10_cohort.json" \
  --output "$ROOT/v2/reports/go10_v2_release_results.json"
```

To verify the published archives themselves:

```bash
python3 "$ROOT/tools/package_oracle_gym_release.py" --version v2
sha256sum "$ROOT"/v2/published_tests/*/oracle_tests.tar.gz
```

Compare those hashes with [`PUBLISHED_ARTIFACTS.json`](PUBLISHED_ARTIFACTS.json).
