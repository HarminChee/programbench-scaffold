# ProgramBench Short-Term Execution Plan - 2026-06-24

## Current Goal

Run Robin's Phase-1 upper-bound experiment:

1. Run original ProgramBench + mini-swe-agent on a small dev set.
2. Inspect baseline trajectories and failure modes.
3. Inject oracle tests as agent-readable specs.
4. Rerun the same agent with the same model and budget.
5. Compare scores and trajectories to estimate the value of perfect specs.

## Dev Set

Primary tasks:

- `sclevine__yj.8016400`
- `multiprocessio__dsq.c3ae0ba`
- `rs__jplot.2a54bcc`

Alternates:

- `sirwart__ripsecrets.34c9e03`
- `cmatsuoka__figlet.202a0a8`
- `mgdm__htmlq.6e31bc8`

## What Is Already Prepared

- Task selection report: `reports/programbench_task_selection.md`
- Oracle specs:
  - `reports/oracle_specs/sclevine__yj.8016400.oracle_spec.md`
  - `reports/oracle_specs/multiprocessio__dsq.c3ae0ba.oracle_spec.md`
  - `reports/oracle_specs/rs__jplot.2a54bcc.oracle_spec.md`
- Oracle mini-swe configs:
  - `reports/oracle_specs/sclevine__yj.8016400.mini_swe_oracle_config.yaml`
  - `reports/oracle_specs/multiprocessio__dsq.c3ae0ba.mini_swe_oracle_config.yaml`
  - `reports/oracle_specs/rs__jplot.2a54bcc.mini_swe_oracle_config.yaml`
- Run/eval orchestrator: `tools/programbench_run_upper_bound.py`
- Comparison/failure analysis: `tools/programbench_compare_upper_bound.py`
- Generated command script: `reports/programbench_upper_bound_runs/run_commands.sh`
- HF test blobs for `yj`, `dsq`, and `jplot` are synced locally.

## Mac Smoke Findings

- Docker Desktop can run `linux/amd64` containers through emulation.
- ProgramBench CLI works locally.
- mini-swe-agent can start ProgramBench cleanroom containers, but slowly.
- `yj` `task_cleanroom_v6` took about 132 seconds to start on Mac emulation after increasing mini-swe's Docker `pull_timeout`.
- The model run is currently blocked: Anthropic API returns `credit balance is too low`.

Conclusion: MacBook is useful for scaffold development and smoke tests, but not for real baseline/oracle runs. Use a Linux x86-64 server for the actual experiment.

## Server Requirements

- Linux x86-64
- Docker working without sudo friction, or user in the Docker group
- `uv`
- network access to DockerHub and HuggingFace for images/blobs
- a working model API key with enough credits

Recommended model for first run:

- `anthropic/claude-3-5-haiku-20241022`

Use the exact same model for baseline and oracle-spec runs.

## How To Run On Server

From `/Users/harmin/Desktop/programbench` or the copied workspace root:

```bash
python3 tools/programbench_run_upper_bound.py --stage prepare
```

Then run baseline and oracle-spec agents:

```bash
python3 tools/programbench_run_upper_bound.py --stage agent --yes-run-agent
```

Then evaluate both variants:

```bash
python3 tools/programbench_run_upper_bound.py --stage eval
```

Then generate the comparison report:

```bash
python3 tools/programbench_compare_upper_bound.py
```

Outputs:

- `reports/programbench_upper_bound_runs/baseline/`
- `reports/programbench_upper_bound_runs/oracle_spec/`
- `reports/programbench_upper_bound_runs/upper_bound_comparison.md`
- `reports/programbench_upper_bound_runs/upper_bound_comparison.json`

## What To Look For

Score table:

```text
task | baseline score | oracle-spec score | delta | main failure modes
```

Trajectory checks:

- Did the baseline read README/help?
- Did it run enough reference executable probes?
- Did it test invalid flags/error paths?
- Did it cover stdin/file inputs?
- Did it begin implementation before understanding behavior?
- Did oracle-spec reduce repeated guessing or missing edge cases?

Failure categories:

- `flags/help/usage`
- `stdin/file/io`
- `stdout/stderr/exit`
- `format/conversion/query`
- `edge/order/unicode`
- `runtime/build`

## Decision Rule

If oracle-spec improves hidden-test score or visibly improves trajectories, then the project direction is validated:

> The core bottleneck is spec recovery. Our later scaffold should mine black-box behavioral specs that approximate oracle tests.

If oracle-spec does not improve much, then the bottleneck may be implementation ability, dependency choice, or repair strategy rather than spec exposure.
