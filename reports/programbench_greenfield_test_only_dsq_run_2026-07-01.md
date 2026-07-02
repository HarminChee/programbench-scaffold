# ProgramBench Greenfield Test-Only Run: dsq

Date: 2026-07-01

## Setup

- Task: `multiprocessio__dsq.c3ae0ba`
- Agent: `mini-swe-agent`
- Model: `bedrock/arn:aws:bedrock:us-west-2:497589205881:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- Runtime config: `configs/programbench_mac_smoke.yaml`
- Step limit: 30
- Cost limit: 0.30
- Test-only config: `configs/programbench_greenfield_test_only.yaml`
- Sanitized tests: `reports/test_bundles/multiprocessio__dsq.c3ae0ba/oracle_tests`
- Output: `reports/programbench_greenfield_test_runs/dsq_test_only`

## Agent Result

| item | value |
| --- | ---: |
| exit status | `Submitted` |
| API calls | 29 |
| observed cost | `$0.2395` |
| submission produced | yes |
| submission contains `oracle_tests` | no |
| submission contains `.git` | no |

The fixed runner correctly excluded the injected oracle tests from
`submission.tar.gz`, so the official eval produced no unexpected-test warnings.

## Eval Result

| variant | passed / active tests | pass rate | rounded score | notes |
| --- | ---: | ---: | ---: | --- |
| original baseline | 0 / 542 | 0.00% | 0 | compile failed |
| greenfield test-only | 301 / 542 | 55.54% | 56 | executable tests visible before coding |
| compact oracle-spec + architecture plan | 337 / 542 | 62.18% | 62 | previous best scaffold variant |

Net result:

- +301 tests over baseline
- +55.54 percentage points over baseline
- 36 tests below compact oracle-spec + architecture plan

## What The Agent Did

The trajectory shows the agent used the injected executable tests directly:

- read `README.md`
- read `oracle_tests/README.md`
- listed oracle branches
- selected branch `5b5334cfb249`
- read `eval/README.md`
- inspected tests:
  - `test_basic_functionality.py`
  - `test_stdin_and_piping.py`
  - `test_exact_output.py`
- read expected outputs:
  - `help_output.txt`
  - `version_output.txt`
- implemented `dsq.py` in Python
- created `compile.sh`
- ran local smoke commands and some pytest commands
- committed and submitted

The implementation strategy was pragmatic:

- Python script executable
- `sqlite3` for SQL execution
- CSV/JSON-style input loading
- `{}` and `{N}` query placeholders
- JSON output by default
- `--pretty` and `--schema` support

## Behavioral Profile

Greenfield test-only passed many functional groups:

| group | greenfield test-only |
| --- | ---: |
| `test_query` | 30 / 37 |
| `test_basic_invocation` | 21 / 25 |
| `test_sql_features` | 20 / 23 |
| `test_advanced_features` | 19 / 25 |
| `test_stdin_file` | 15 / 26 |
| `test_formats` | 15 / 41 |
| `test_complex_queries` | 15 / 19 |
| `test_error_handling` | 15 / 18 |
| `test_edge_cases` | 14 / 15 |

Weak groups:

| group | greenfield test-only |
| --- | ---: |
| `test_errors` | 2 / 31 |
| `test_arg_parsing` | 1 / 15 |
| `test_file_formats` | 7 / 21 |
| `test_output` | 14 / 41 |

Compared with compact oracle-spec + architecture plan:

- test-only is slightly better on `test_query` and `test_formats`
- architecture plan is much better on `test_errors` and `test_arg_parsing`

## Interpretation

This is a strong positive signal for Robin's upper-bound experiment.

Unlike `yj`, direct executable tests helped the agent avoid a compile failure
and build a useful implementation. The test-only setting turned a 0-score
baseline into 301/542 passing tests.

However, the previous compact spec + architecture plan still did better:
337/542. This suggests the next scaffold should not simply dump executable
tests. It should add a planning/triage layer that turns tests into priorities:

- implement core data/query path first
- separately enforce exact CLI/error/exit-code semantics
- warn about high-risk groups such as argument parsing and error strings
- keep injected tests out of final submission

## Takeaway

For `dsq`, executable tests are very useful as upper-bound specs. But the gap
between 301 and 337 shows that raw tests still need scaffolding to become
implementation-ready guidance.
