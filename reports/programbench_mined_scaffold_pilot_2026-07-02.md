# ProgramBench Fair Mined-Scaffold Pilot

## Summary

- Tasks evaluated: 3
- Original baseline average on these tasks: 26.62%
- Test-only upper-bound average on these tasks: 52.13%
- Fair mined-scaffold average on these tasks: 49.77%
- Mined scaffold improved over baseline: 2/3
- Mined scaffold reached/exceeded test-only run: 1/3

The mined scaffold is fair: it uses only bundled documentation plus black-box executions of the reference executable. It does not read official tests, test metadata, or source code.

## Result Table

| task | baseline | test-only | mined scaffold | mined vs baseline | mined vs test-only | status | note |
|---|---:|---:|---:|---:|---:|---|---|
| `sclevine__yj.8016400` | 74/767 (9.65%) | 246/767 (32.07%) | 276/767 (35.98%) | +26.34 | +3.91 | Submitted | mined scaffold exceeded this run's test-only upper-bound run; likely helped planning plus preserved prompt exploration |
| `rs__jplot.2a54bcc` | 135/583 (23.16%) | 401/583 (68.78%) | 396/583 (67.92%) | +44.77 | -0.86 | Submitted | mined scaffold recovers part of the spec gap |
| `multiprocessio__dsq.c3ae0ba` | 255/542 (47.05%) | 301/542 (55.54%) | 246/542 (45.39%) | -1.66 | -10.15 | LimitsExceeded | negative pilot; current generic mined specs are not rich enough for this task |

## Trajectory Signals

| task | mined route | mined notes | cost | calls |
|---|---|---|---:|---:|
| `sclevine__yj.8016400` | go->shell | tried unavailable external dependencies | 0.288 | 29 |
| `rs__jplot.2a54bcc` | go | none obvious | 0.210 | 29 |
| `multiprocessio__dsq.c3ae0ba` | python->go->python | tried unavailable external dependencies | 0.346 | 24 |

## Interpretation

- `yj`: mined docs/probes exposed exact CLI and stdin conversion behavior; the agent improved strongly over baseline.
- `jplot`: documentation summary plus option/error probes nearly matched the test-only score, suggesting docs+black-box process evidence can recover much of the spec gap for some CLI tools.
- `dsq`: the current generic scaffold underperformed baseline. The mined spec captured invocation and basic file behavior, but not enough SQL/query and multi-format semantics. This is the next target for smarter probing and prioritization.

Next fair-scaffold iteration should add task-aware probe synthesis from docs/help: generate concrete CSV/JSON/SQL examples for data-query tools, conversion matrices for format-conversion tools, and option-argument probes for flags that require values.
