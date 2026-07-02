# ProgramBench Dev-Set Progress

Date: 2026-06-24

## Research Direction

We are following Robin's Phase-1 plan:

1. Run the original ProgramBench / mini-swe-agent setting on a small dev set.
2. Inspect trajectories to understand why agents fail.
3. Inject oracle-derived specs as an upper-bound reference.
4. Measure the gap between baseline and oracle/spec-assisted agents.
5. Improve the scaffold by mining and presenting specs in a more compact, implementation-ready form.

The current scaffold direction is **compact oracle-spec + spec-to-architecture planning**. The goal is not to train a model. The goal is to expose better behavioral specs and implementation guidance to an existing coding agent.

## Current Dev Set

| Task | Status |
|---|---|
| `sclevine__yj.8016400` | Fully run locally: baseline, full oracle spec, compact oracle spec, compact oracle + architecture plan |
| `multiprocessio__dsq.c3ae0ba` | Fully run locally after increasing `PROGRAMBENCH_DOCKER_RUN_TIMEOUT` to 900 seconds |
| `rs__jplot.2a54bcc` | Baseline and two architecture-plan agents run; timeout-aware architecture-plan v2 official eval completed |
| `sirwart__ripsecrets.34c9e03` | Fully run locally: baseline and compact oracle spec + architecture plan |
| `cmatsuoka__figlet.202a0a8` | Baseline and compact oracle spec + architecture plan agents run; official eval blocked locally by slow/unstable Mac Docker eval |

## yj Results

Task: `sclevine__yj.8016400`

| Variant | Agent status | Official score | Active pass rate | Notes |
|---|---:|---:|---:|---|
| baseline | Submitted | 32 | 242 / 767 = 31.55% | Original mini-swe-agent setting |
| full oracle-test-name spec | LimitsExceeded | 0 | 0 / 767 | Failed to compile due external Go module issue |
| compact oracle spec | LimitsExceeded | 42 | 320 / 767 = 41.72% | First positive oracle-spec signal |
| compact oracle spec + architecture plan | LimitsExceeded | 53 | 405 / 767 = 52.80% | Best result so far |

Key evidence:

- Compact oracle spec improved official score from 32 to 42.
- Adding an architecture plan improved official score from 42 to 53.
- Relative to baseline, the best scaffold improves active tests passed from 242 to 405, a gain of 163 active tests.

Trajectory-level evidence:

| Variant | Messages | Commands | External dependency attempts | Compile/build calls |
|---|---:|---:|---:|---:|
| compact oracle spec | 81 | 39 | 2 | 6 |
| compact oracle spec + architecture plan | 59 | 28 | 0 | 2 |

Interpretation:

The architecture-plan scaffold changed agent behavior, not just prompt length. It reduced wasted dependency attempts, reduced command count, produced a compiling candidate, and improved official ProgramBench score.

## dsq Status

Task: `multiprocessio__dsq.c3ae0ba`

Baseline agent:

- Status: `LimitsExceeded`
- Observed cost: about `$0.32`
- Main failure mode from trajectory: the agent tried to implement `dsq` in Go with a SQLite driver dependency, but the task environment has no internet access.
- It switched to Python `sqlite3` only near the end, too late to finish cleanly.

Architecture-plan agent:

- Status: `Submitted`
- Observed cost: about `$0.49`
- The scaffold explicitly recommended a Python standard-library implementation using `sqlite3`, `csv`, and `json`.
- The agent followed that route, avoided external dependency attempts, compiled successfully, and passed its own smoke tests for CSV queries, joins, pretty output, help, and basic `SELECT *`.

Trajectory-level comparison:

| Variant | Messages | Commands | External dependency attempts | Python/sqlite route evidence |
|---|---:|---:|---:|---:|
| baseline | 51 | 24 | 4 | 4 late mentions |
| architecture plan | 90 | 44 | 0 | 5 direct uses |

Official eval result:

The first local eval attempts timed out while starting the task Docker image:

```text
ERROR: TimeoutExpired while starting programbench/multiprocessio_1776_dsq.c3ae0ba:task
```

This was a MacBook Docker/Rosetta startup issue. Increasing `PROGRAMBENCH_DOCKER_RUN_TIMEOUT` from 300 to 900 seconds allowed official eval to complete.

| Variant | Agent status | Official score | Active pass rate | Notes |
|---|---:|---:|---:|---|
| baseline | LimitsExceeded | 0 | 0 / 542 = 0.00% | `compile_failed`; tried Go SQLite dependency path |
| compact oracle spec + architecture plan | Submitted | 62 | 337 / 542 = 62.18% | Python stdlib `sqlite3/csv/json` route |

This is a second strong positive signal. The architecture plan did exactly what it was supposed to do: it prevented the agent from wasting budget on unavailable Go SQLite dependencies and routed it to a self-contained implementation strategy.

## Implemented Scaffold Components

- `tools/programbench_oracle_specs.py`
  Converts ProgramBench `tests.json` into compact behavior-area specs.

- `tools/programbench_architecture_plan.py`
  Converts oracle-spec JSON into an implementation plan. It now includes task-specific plans for:
  - `sclevine/yj`
  - `multiprocessio/dsq`
  - `rs/jplot`
  - `sirwart/ripsecrets`
  - `cmatsuoka/figlet`
  - `mgdm/htmlq`

- `tools/programbench_make_oracle_config.py`
  Injects oracle specs and optional architecture plans into mini-swe-agent config.

- `tools/programbench_compare_upper_bound.py`
  Computes ProgramBench-style active-test pass rates with ignored-test filtering.

- `tools/programbench_trajectory_analyzer.py`
  Reads `.traj.json` and `.eval.json` files and produces automated trajectory tables: score, pass count, cost, API calls, implementation route, reference probing count, invalid/error probes, stdin probes, dependency attempts, compile calls, and baseline-vs-scaffold interpretation.

## jplot Status

Task: `rs__jplot.2a54bcc`

Baseline agent:

- Status: `Submitted`
- Observed cost: about `$0.26`
- Trajectory route: Go implementation
- Official eval status: not completed locally. Two attempts ran for more than 14 minutes and got stuck/retried around URL/interval-related tests, so the run was manually interrupted to avoid blocking the Mac.

Architecture-plan agent:

- Status: `Submitted`
- Observed cost: about `$0.75`
- Trajectory route: Python standard-library implementation, matching the scaffold plan
- Official score: 20
- Active pass rate: 116 / 583 = 19.90%

Timeout-aware architecture-plan v2:

- Status: `Submitted`
- Observed cost: about `$0.43`
- Added scaffold rule: URL/interval loops must terminate quickly under benchmark-like non-interactive conditions.
- Official score: 42
- Active pass rate: 243 / 583 = 41.68%

Trajectory-level comparison:

| Variant | Messages | Commands | Python route evidence | Go route evidence | URL probes |
|---|---:|---:|---:|---:|---:|
| baseline | 58 | 28 | 0 | 9 | 0 |
| architecture plan | 100 | 49 | 14 | 0 | 1 |
| timeout-aware architecture plan v2 | 92 | 45 | 0 | 4 | 1 |

Interpretation:

`jplot` is harder for the current scaffold, but the timeout-aware rule produced a useful improvement: official score increased from 20 to 42 while cost decreased from about `$0.75` to `$0.43`. Baseline official score is still missing because its eval repeatedly hung/retried around URL/interval behavior, but the scaffold iteration is clearly measurable within the oracle-assisted variants.

## Additional Tasks

### ripsecrets

Task: `sirwart__ripsecrets.34c9e03`

| Variant | Agent status | Official score | Active pass rate | Notes |
|---|---:|---:|---:|---|
| baseline | Submitted | 40 | 242 / 611 = 39.61% | Baseline explored little reference behavior and attempted a Rust/Python mixed route |
| compact oracle spec + architecture plan | Submitted | 73 | 443 / 611 = 72.50% | Scaffold pushed a Python stdlib scanner route and broader reference probing |

Key evidence:

- Official score improved from 40 to 73.
- Active tests passed improved from 242 to 443, a gain of 201 active tests.
- Trajectory analyzer identifies the likely mechanism: baseline route was `python->rust->python->shell`, had limited reference probing, and made an external dependency attempt; scaffold route was direct `python`, with more reference probing and no dependency attempt.

Failure reduction by category:

| Variant | Main remaining failures |
|---|---|
| baseline | stdout/stderr/exit: 340; stdin/file/io: 288; edge/order/unicode: 65 |
| compact oracle spec + architecture plan | stdout/stderr/exit: 158; stdin/file/io: 127; flags/help/usage: 21 |

Interpretation:

This is the third strong positive upper-bound signal. It is especially useful because it is not a format-conversion task like `yj` or `dsq`; it is a secret-scanning CLI with regexes, recursion, ignore rules, and exit-code behavior. The same scaffold idea still helps substantially.

### figlet

Task: `cmatsuoka__figlet.202a0a8`

| Variant | Agent status | Official score | Notes |
|---|---:|---:|---|
| baseline | Submitted | Not completed locally | Agent trajectory completed; official eval repeatedly retried/failed to read branch results under Mac Docker emulation |
| compact oracle spec + architecture plan | Submitted | Not completed locally | Agent trajectory completed; scaffold increased reference probing from 18 to 36 commands |

Mac limitation:

`figlet` has 872 active tests and heavy font/layout behavior. Local official eval on Mac amd64 emulation got stuck/retried around branch result collection, so it was interrupted to avoid blocking the machine. This task should be rerun on a Linux x86-64 server before drawing score conclusions.

## Next Actions

1. Keep `PROGRAMBENCH_DOCKER_RUN_TIMEOUT=900` for Mac eval.
   This was necessary for `dsq` and should be part of the Mac smoke workflow.

2. Analyze remaining `jplot` and `ripsecrets` failures.
   Timeout-aware v2 improved from 20 to 42, but still passes only 243/583 active tests. The next plan should target stdout/stderr/exit behavior, stdin/file behavior, and JSON/field-spec parsing clusters.
   For `ripsecrets`, the biggest remaining clusters are stdout/stderr/exit and stdin/file/io behavior.

3. Use the trajectory analyzer as the default reporting layer.
   The clean consolidated report is `reports/programbench_trajectory_analysis.md`.

4. Rerun heavy evals on Linux x86-64 when available.
   `figlet`, `jplot` baseline, and eventually `htmlq` are likely too slow or unstable for reliable Mac-only evaluation.

5. Keep the proposal direction focused:
   our contribution is not "more tests in prompt"; it is compact behavioral spec mining plus architecture scaffolding for black-box program reconstruction agents.
