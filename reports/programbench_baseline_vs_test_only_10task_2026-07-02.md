# ProgramBench Original Baseline vs Test-Only Scaffold

## Summary

- Original baseline average: 20.74%
- Test-only scaffold average: 40.78%
- Average delta: +20.05 points
- Improved tasks: 8/10
- Regressed tasks: 2/10
- Baseline compile failures: 3/10

## Result Table

| task | lang | baseline | test-only | delta | baseline status | test-only status | note |
|---|---:|---:|---:|---:|---|---|---|
| `sirwart__ripsecrets.34c9e03` | rs | 0/611 (0.00%) | 408/611 (66.78%) | +66.78 | LimitsExceeded | LimitsExceeded | baseline eval error: compile_failed; baseline tried unavailable dependencies; test-only specs gave strong positive signal |
| `wfxr__csview.8ac4de0` | rs | 208/335 (62.09%) | 258/335 (77.01%) | +14.93 | Submitted | Submitted | baseline tried unavailable dependencies; test-only specs gave strong positive signal |
| `wfxr__code-minimap.0ddeea5` | rs | 85/313 (27.16%) | 73/313 (23.32%) | -3.83 | Submitted | LimitsExceeded | baseline tried unavailable dependencies; test-only underperformed baseline; likely raw tests distracted or plan changed |
| `clog-tool__clog-cli.7066cba` | rs | 0/575 (0.00%) | 97/575 (16.87%) | +16.87 | Submitted | LimitsExceeded | baseline eval error: compile_failed; baseline tried unavailable dependencies; test-only specs gave strong positive signal |
| `drew-alleman__datasurgeon.d257cee` | rs | 0/502 (0.00%) | 184/502 (36.65%) | +36.65 | Submitted | LimitsExceeded | baseline eval error: compile_failed; baseline tried unavailable dependencies; test-only specs gave strong positive signal |
| `sclevine__yj.8016400` | go | 74/767 (9.65%) | 246/767 (32.07%) | +22.43 | LimitsExceeded | LimitsExceeded | baseline tried unavailable dependencies; test-only specs gave strong positive signal |
| `multiprocessio__dsq.c3ae0ba` | go | 255/542 (47.05%) | 301/542 (55.54%) | +8.49 | Submitted | Submitted | baseline tried unavailable dependencies; test-only specs gave modest improvement |
| `rs__jplot.2a54bcc` | go | 135/583 (23.16%) | 401/583 (68.78%) | +45.63 | Submitted | Submitted | test-only specs gave strong positive signal |
| `cmatsuoka__figlet.202a0a8` | c | 118/872 (13.53%) | 144/872 (16.51%) | +2.98 | Submitted | Submitted | test-only specs gave modest improvement |
| `cslarsen__jp2a.61d205f` | c | 156/631 (24.72%) | 90/631 (14.26%) | -10.46 | LimitsExceeded | Submitted | baseline tried unavailable dependencies; test-only underperformed baseline; likely raw tests distracted or plan changed |

## Trajectory Signals

| task | baseline route | baseline notes | test-only route | test-only notes |
|---|---|---|---|---|
| `sirwart__ripsecrets.34c9e03` | rust->shell->rust->shell | tried unavailable external dependencies; implementation started very early | rust->shell->python->shell | limited reference probing; tried unavailable external dependencies; implementation started very early |
| `wfxr__csview.8ac4de0` | rust->shell->python->shell->python | tried unavailable external dependencies; implementation started very early | rust->shell->python->shell->python | limited reference probing; tried unavailable external dependencies; implementation started very early |
| `wfxr__code-minimap.0ddeea5` | python->rust->python->shell->python | tried unavailable external dependencies; implementation started very early | python | limited reference probing; implementation started very early |
| `clog-tool__clog-cli.7066cba` | rust->go->rust | tried unavailable external dependencies | rust->shell->rust | limited reference probing; tried unavailable external dependencies; implementation started very early |
| `drew-alleman__datasurgeon.d257cee` | rust->go->rust->shell | tried unavailable external dependencies | python | limited reference probing; implementation started very early |
| `sclevine__yj.8016400` | go->python->go | tried unavailable external dependencies | go->shell | limited reference probing; tried unavailable external dependencies; implementation started very early |
| `multiprocessio__dsq.c3ae0ba` | python->go->python->shell->python | tried unavailable external dependencies | python->shell->python | tried unavailable external dependencies; implementation started very early |
| `rs__jplot.2a54bcc` | go | none obvious | go->shell->go | limited reference probing; implementation started very early |
| `cmatsuoka__figlet.202a0a8` | c | none obvious | c->shell | limited reference probing; implementation started very early |
| `cslarsen__jp2a.61d205f` | c->python | tried unavailable external dependencies | c->shell | limited reference probing; tried unavailable external dependencies; implementation started very early |

## Interpretation

The original baseline is substantially weaker than the test-only upper-bound setting on this dev set. The most important signal is not just score improvement: three baseline runs compile-failed, and several successful baseline submissions stayed far below test-only scores. This supports Robin's hypothesis that missing or poorly exposed specs are a major bottleneck.

The two regressions (`code-minimap`, `jp2a`) are also useful: raw executable tests can distract the agent or push it toward a worse implementation plan. A fair scaffold should therefore summarize and prioritize mined behavior instead of dumping raw observations.
