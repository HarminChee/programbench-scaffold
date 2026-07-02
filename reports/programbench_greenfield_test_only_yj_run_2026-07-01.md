# ProgramBench Greenfield Test-Only Run: yj

Date: 2026-07-01

## Setup

- Task: `sclevine__yj.8016400`
- Agent: `mini-swe-agent`
- Model: `bedrock/arn:aws:bedrock:us-west-2:497589205881:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- Runtime config: `configs/programbench_mac_smoke.yaml`
- Step limit: 30
- Cost limit: 0.30
- Test-only config: `configs/programbench_greenfield_test_only.yaml`
- Sanitized tests: `reports/test_bundles/sclevine__yj.8016400/oracle_tests`
- Output: `reports/programbench_greenfield_test_runs/yj_test_only`

## Commands

Agent run:

```bash
env -u ANTHROPIC_API_KEY CLAUDE_CODE_USE_BEDROCK=1 \
  AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2 AWS_PROFILE=default \
  uv run --with mini-swe-agent --with boto3 python \
  /Users/harmin/Desktop/programbench/tools/programbench_run_greenfield_tests.py \
  --workspace-root /Users/harmin/Desktop/programbench \
  --task sclevine__yj.8016400 \
  --output reports/programbench_greenfield_test_runs/yj_test_only \
  --model 'bedrock/arn:aws:bedrock:us-west-2:497589205881:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0' \
  --yes-run-agent \
  --redo-existing
```

Clean official eval:

```bash
uv run programbench eval \
  /Users/harmin/Desktop/programbench/reports/programbench_greenfield_test_runs/yj_test_only \
  --filter '^sclevine__yj\.8016400$' \
  --workers 1 \
  --branch-workers 1 \
  --docker-cpus 6 \
  --force
```

## Agent Result

| item | value |
| --- | ---: |
| exit status | `LimitsExceeded` |
| API calls | 30 |
| observed cost | `$0.2726` |
| submission produced | yes |

The run did not explicitly submit before the step limit, but the runner copied
the current workspace as a submission.

## Eval Result

| variant | passed / active tests | pass rate | rounded score |
| --- | ---: | ---: | ---: |
| original baseline | 242 / 767 | 31.55% | 32 |
| greenfield test-only | 246 / 767 | 32.07% | 32 |
| compact oracle-spec | 320 / 767 | 41.72% | 42 |
| compact oracle-spec + architecture plan | 405 / 767 | 52.80% | 53 |

Net result for this exact-budget test-only run:

- +4 tests over baseline
- +0.52 percentage points
- no rounded-score improvement

## Important Bug Found And Fixed

The first eval attempt produced:

```text
Score 30, ERRORS: WARN: 5
```

Root cause: the initial runner copied `oracle_tests` into `submission.tar.gz`.
During official eval, some branch pytest commands collected the injected oracle
tests as extra tests, producing thousands of unexpected JUnit test names.

Fix:

- updated `tools/programbench_run_greenfield_tests.py`
- `copy_submission()` now excludes:
  - `./oracle_tests`
  - `./.git`
- re-packed the existing submission without rerunning the agent
- reran official eval

The clean eval result is:

```text
Score 32, 767 tests
```

## What The Agent Did

The trajectory shows the agent used the injected tests:

- read `README.md`
- read `oracle_tests/README.md`
- listed `oracle_tests/branches`
- inspected branch `09b0bec043ae`
- read multiple pytest files such as `test_json.py`, `test_errors.py`,
  `conftest.py`
- wrote a Go implementation
- attempted external packages for TOML/HCL/YAML
- backed off to a smaller YAML/JSON-focused implementation
- wrote `AGENT_REPORT.md`

The final agent report says the implementation covers:

- basic YAML to JSON
- JSON to YAML
- JSON identity
- YAML identity
- `-i` JSON indentation
- `-e` HTML escaping
- help/version

Missing:

- TOML support
- HCL support
- exact error messages
- `-n` and `-k` semantics
- dependency/vendor handling

## Behavioral Changes Vs Baseline

Improved groups:

| group | improvement signal |
| --- | --- |
| `test_errors` | 12/45 -> 26/45 |
| `test_edge_cases` | 32/40 -> 37/40 |
| `test_yaml_conversions` | 13/29 -> 21/29 |
| `test_yaml` | 0/29 -> 5/29 |
| `test_roundtrip` | 3/26 -> 7/26 |

Regressed groups:

| group | regression signal |
| --- | --- |
| `test_order` | 7/40 -> 1/40 |
| `test_toml` | 6/39 -> 0/39 |
| `test_help_usage` | 28/31 -> 27/31 |

Overall:

- 71 tests improved relative to baseline.
- 67 tests regressed relative to baseline.
- Net gain was only +4 active tests.

## Interpretation

This is a useful negative/neutral result.

Executable tests alone did make the agent inspect more concrete behavioral
requirements, and it improved YAML/error/edge-case behavior. However, with the
same tight budget, the agent spent many steps reading tests and handling Go
dependency issues. It did not produce TOML/HCL support and regressed some
baseline behaviors.

This supports a sharper research claim:

> Directly exposing executable tests is not automatically enough. The scaffold
> needs to convert tests into budget-aware, implementation-ready guidance, and
> possibly help keep oracle tests out of the final submission.

This also explains why the previous compact oracle-spec + architecture plan
performed much better on `yj`: that scaffold compressed the test signal into an
implementation strategy instead of making the agent discover strategy from raw
tests under a tight step budget.

## Next

Run `dsq` with the fixed runner. If `dsq` shows a larger test-only gain, the
result may be task-dependent. If it also stays flat, the immediate conclusion is
that raw executable tests need an additional planning/scaffolding layer to be
useful.
