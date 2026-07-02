# ProgramBench Robin Plan Status - 2026-06-21

## Direction

We should follow Robin's Phase-1 framing:

1. Run the original ProgramBench + mini-swe-agent setting on a small dev set.
2. Inspect agent trajectories to understand failure modes.
3. Inject oracle tests as specs and rerun the same agent to estimate the upper bound from perfect specs.
4. Later, build black-box scaffolding that tries to mine specs close to that oracle upper bound.

## Environment Status

- MacBook + Docker Desktop can run `linux/amd64` containers through emulation.
- Local ProgramBench CLI works from `external/ProgramBench`.
- mini-swe-agent 2.4.2 includes a `mini-extra programbench` runner.
- The runner can start a real ProgramBench cleanroom container on this Mac.
- Current blocker for actual baseline trajectories: `ANTHROPIC_API_KEY` exists, but Anthropic API returned `credit balance is too low`.

## Dev-Set Recommendation

Start with:

1. `sclevine__yj.8016400`
2. `multiprocessio__dsq.c3ae0ba`
3. `rs__jplot.2a54bcc`

Keep as alternates:

- `sirwart__ripsecrets.34c9e03`
- `cmatsuoka__figlet.202a0a8`
- `mgdm__htmlq.6e31bc8`

Reason: `yj`, `dsq`, `jplot`, and `ripsecrets` are easy tasks with manageable oracle-test counts and CLI behavior. `figlet` and `htmlq` are useful but have larger spec surfaces.

## Completed Artifacts

- Task inspection script: `tools/programbench_task_inspect.py`
- Oracle-test-to-spec script: `tools/programbench_oracle_specs.py`
- Oracle-spec mini-swe config generator: `tools/programbench_make_oracle_config.py`
- Mac smoke mini-swe config: `configs/programbench_mac_smoke.yaml`
- Task selection report: `reports/programbench_task_selection.md`
- `yj` oracle spec: `reports/oracle_specs/sclevine__yj.8016400.oracle_spec.md`
- `yj` oracle mini-swe config: `reports/oracle_specs/sclevine__yj.8016400.mini_swe_oracle_config.yaml`

## Smoke Results

### ProgramBench / Docker

- `docker run --platform linux/amd64 alpine:3.20 uname -m` returned `x86_64`.
- ProgramBench CLI worked: `uv run programbench --help`.
- `yj` HF test blobs synced successfully.
- `yj` Docker images exist:
  - `programbench/sclevine_1776_yj.8016400:task`
  - `programbench/sclevine_1776_yj.8016400:task_cleanroom`
  - `programbench/sclevine_1776_yj.8016400:task_cleanroom_v6`

### Cleanroom Inspection

Inside `yj` cleanroom:

- Visible files: `README.md`, `LICENSE`, `logo.png`, `executable`.
- `executable` is executable-only and not readable by the agent.
- Go toolchain is available.
- `./executable -h` prints the documented conversion flags.
- Invalid flags return code 1 and print usage plus an error.
- Simple stdin behavior works: `{"a":1}` with `-jy` outputs `a: 1`.

### Baseline Attempt

Command started the cleanroom container successfully, but model calls failed:

```bash
uv run --with mini-swe-agent mini-extra programbench --filter 'sclevine__yj.8016400' --output /Users/harmin/Desktop/programbench/reports/miniswe_programbench_yj_baseline_smoke --workers 1 --model anthropic/claude-3-5-haiku-20241022 --config /Users/harmin/.cache/uv/archive-v0/MLfEtw9t2PoRakwr/lib/python3.13/site-packages/minisweagent/config/benchmarks/programbench.yaml --config /Users/harmin/Desktop/programbench/configs/programbench_mac_smoke.yaml --redo-existing
```

Failure reason: Anthropic API rejected the request because the credit balance is too low.

## Next Commands Once Model Access Works

Baseline:

```bash
cd /Users/harmin/Desktop/programbench/external/ProgramBench
uv run --with mini-swe-agent mini-extra programbench --filter 'sclevine__yj.8016400' --output /Users/harmin/Desktop/programbench/reports/miniswe_programbench_yj_baseline --workers 1 --model anthropic/claude-3-5-haiku-20241022 --config /Users/harmin/.cache/uv/archive-v0/MLfEtw9t2PoRakwr/lib/python3.13/site-packages/minisweagent/config/benchmarks/programbench.yaml --config /Users/harmin/Desktop/programbench/configs/programbench_mac_smoke.yaml --redo-existing
```

Oracle-spec upper-bound:

```bash
cd /Users/harmin/Desktop/programbench/external/ProgramBench
uv run --with mini-swe-agent mini-extra programbench --filter 'sclevine__yj.8016400' --output /Users/harmin/Desktop/programbench/reports/miniswe_programbench_yj_oracle_spec --workers 1 --model anthropic/claude-3-5-haiku-20241022 --config /Users/harmin/.cache/uv/archive-v0/MLfEtw9t2PoRakwr/lib/python3.13/site-packages/minisweagent/config/benchmarks/programbench.yaml --config /Users/harmin/Desktop/programbench/reports/oracle_specs/sclevine__yj.8016400.mini_swe_oracle_config.yaml --config /Users/harmin/Desktop/programbench/configs/programbench_mac_smoke.yaml --redo-existing
```

Evaluation after each run:

```bash
cd /Users/harmin/Desktop/programbench/external/ProgramBench
uv run programbench eval /Users/harmin/Desktop/programbench/reports/miniswe_programbench_yj_baseline --filter 'sclevine__yj.8016400' --workers 1 --branch-workers 1 --docker-cpus 2 --force
uv run programbench eval /Users/harmin/Desktop/programbench/reports/miniswe_programbench_yj_oracle_spec --filter 'sclevine__yj.8016400' --workers 1 --branch-workers 1 --docker-cpus 2 --force
```
