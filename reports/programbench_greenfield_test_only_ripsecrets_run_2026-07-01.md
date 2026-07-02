# ProgramBench Greenfield Test-Only Run: ripsecrets

Date: 2026-07-01

## Setup

- Task: `sirwart__ripsecrets.34c9e03`
- Agent: `mini-swe-agent`
- Model: `bedrock/arn:aws:bedrock:us-west-2:497589205881:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- Runtime config: `configs/programbench_mac_smoke.yaml`
- Step limit: 30
- Cost limit: 0.30
- Test-only config: `configs/programbench_greenfield_test_only.yaml`
- Sanitized tests: `reports/test_bundles/sirwart__ripsecrets.34c9e03/oracle_tests`
- Output: `reports/programbench_greenfield_test_runs/ripsecrets_test_only`

## Agent Result

| item | value |
| --- | ---: |
| exit status | `LimitsExceeded` |
| API calls | 21 |
| observed cost | `$0.3019` |
| submission produced | yes |
| submission contains `oracle_tests` | no |
| submission contains `.git` | no |

## Eval Result

| variant | passed / active tests | pass rate | rounded score |
| --- | ---: | ---: | ---: |
| original baseline | 242 / 611 | 39.61% | 40 |
| greenfield test-only | 408 / 611 | 66.78% | 67 |
| compact oracle-spec + architecture plan | 443 / 611 | 72.50% | 73 |

Net result:

- +166 tests over baseline
- +27.17 percentage points over baseline
- 35 tests below compact oracle-spec + architecture plan

## What The Agent Did

The trajectory shows the agent used the executable tests directly:

- read `README.md`
- read `oracle_tests/README.md`
- inspected branch `ce070847633b`
- read test files:
  - `test_basic.py`
  - `test_patterns.py`
  - `test_ignore.py`
  - `test_features.py`
- first tried a Rust implementation with crates such as `regex`, `clap`,
  `walkdir`, `glob`
- because external crates were not available offline, switched to a Python
  implementation
- wrote `ripsecrets.py`
- generated executable via `compile.sh`
- ran branch tests and a manual secret-detection smoke test

## Behavioral Profile

Groups where greenfield test-only was strong:

| group | greenfield test-only |
| --- | ---: |
| `test_massive_coverage` | 31 / 31 |
| `test_comprehensive_coverage` | 25 / 25 |
| `test_file_handling` | 19 / 19 |
| `test_extensive_patterns` | 17 / 17 |
| `test_ignore` | 25 / 28 |
| `test_ignore_mechanisms` | 20 / 22 |
| `test_secret_detection` | 30 / 43 |
| `test_basic` | 17 / 18 |

Weak groups:

| group | greenfield test-only |
| --- | ---: |
| `test_precommit` | 1 / 38 |
| `test_edge_cases` | 46 / 78 |
| `test_patterns` | 22 / 46 |
| `test_cli` | 18 / 31 |

Compared with compact oracle-spec + architecture plan:

- test-only is stronger on `test_harvest`, `test_basic`,
  `test_output_formatting`, `test_ignore_mechanisms`, and some file handling
- architecture plan is stronger on `test_edge_cases`, `test_patterns`,
  `test_secret_detection`, and `test_precommit`

## Agent-Reported Missing Pieces

The agent's `AGENT_REPORT.md` identified concrete remaining issues:

- Slack token pattern was too rigid.
- GitHub PAT pattern length needed tuning.
- Generic entropy checking was too strict.
- Additional patterns with capture groups were not handled exactly.
- `.secretsignore` glob/directory behavior needed improvement.
- pragma allowlist comments were incomplete.
- strict-ignore mode needed review.
- only-matching output formatting needed exact matching.

## Interpretation

This is a strong positive signal for executable-test upper-bound specs.

The injected tests helped the agent build a useful scanner without reference
binary access and without internet. The score improved from 39.61% to 66.78%.

Still, the compact oracle-spec + architecture plan remains stronger. The main
remaining gap is not core functionality; it is precise pattern semantics, CLI
edge cases, ignore behavior, and precommit-specific behavior. These are exactly
the kinds of details a scaffold should prioritize and summarize for the agent.

## Takeaway

For `ripsecrets`, executable tests are valuable, but raw tests alone still leave
the agent to discover which exact patterns and mode semantics matter. A better
scaffold should extract these into a compact checklist before implementation.
