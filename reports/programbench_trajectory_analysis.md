# ProgramBench Trajectory Analysis

## Run Root: `reports/programbench_upper_bound_runs_arch_plan`

### Variant Metrics

| task | variant | status | score | pass/total | cost | calls | route | cmds | ref | err probes | stdin | deps | compiles | notes |
|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| `sclevine__yj.8016400` | baseline | Submitted | 31.55 | 242/767 | 0.26 | 29 | go->shell->go | 29 | 14 | 25 | 60 | 3 | 8 | tried unavailable external dependencies |
| `sclevine__yj.8016400` | oracle_spec | LimitsExceeded | 52.8 | 405/767 | 0.83 | 28 | go | 28 | 20 | 158 | 181 | 0 | 9 | none obvious |
| `multiprocessio__dsq.c3ae0ba` | baseline | Submitted? | 0.0 | 0/542 | 0.32 | 24 | python->go->python | 24 | 7 | 38 | 74 | 15 | 27 | tried unavailable external dependencies; implementation started very early |
| `multiprocessio__dsq.c3ae0ba` | oracle_spec | Submitted | 62.18 | 337/542 | 0.49 | 44 | python | 44 | 34 | 30 | 23 | 2 | 3 | tried unavailable external dependencies |
| `rs__jplot.2a54bcc` | baseline | Submitted? | None | missing | 0.26 | 28 | go->shell->go->shell | 28 | 7 | 11 | 21 | 2 | 11 | tried unavailable external dependencies |
| `rs__jplot.2a54bcc` | oracle_spec | Submitted | 19.9 | 116/583 | 0.75 | 49 | python->shell->python->shell | 49 | 28 | 187 | 40 | 0 | 3 | none obvious |

### Baseline vs Scaffold Interpretation

| task | baseline | scaffold | delta | why baseline failed | why scaffold helped |
|---|---:|---:|---:|---|---|
| `sclevine__yj.8016400` | 31.55 | 52.8 | 21.25 | spent budget on external dependencies | score improved by 21.25 points; implementation route changed: go->shell->go -> go; reduced external dependency attempts; more reference probing |
| `multiprocessio__dsq.c3ae0ba` | 0.0 | 62.18 | 62.18 | eval error: compile_failed; spent budget on external dependencies | score improved by 62.18 points; implementation route changed: python->go->python -> python; reduced external dependency attempts; more reference probing; more direct compile/implementation path |
| `rs__jplot.2a54bcc` | None | 19.9 | None | eval error: missing_eval_json; spent budget on external dependencies | implementation route changed: go->shell->go->shell -> python->shell->python->shell; reduced external dependency attempts; more reference probing |

## Run Root: `reports/programbench_upper_bound_runs_jplot_timeout_plan`

### Variant Metrics

| task | variant | status | score | pass/total | cost | calls | route | cmds | ref | err probes | stdin | deps | compiles | notes |
|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| `rs__jplot.2a54bcc` | baseline | Submitted? | None | missing | 0.26 | 28 | go->shell->go->shell | 28 | 7 | 11 | 21 | 2 | 11 | tried unavailable external dependencies |
| `rs__jplot.2a54bcc` | oracle_spec | Submitted | 41.68 | 243/583 | 0.43 | 45 | go | 45 | 24 | 31 | 54 | 0 | 4 | none obvious |

### Baseline vs Scaffold Interpretation

| task | baseline | scaffold | delta | why baseline failed | why scaffold helped |
|---|---:|---:|---:|---|---|
| `rs__jplot.2a54bcc` | None | 41.68 | None | eval error: missing_eval_json; spent budget on external dependencies | implementation route changed: go->shell->go->shell -> go; reduced external dependency attempts; more reference probing |

## Run Root: `reports/programbench_upper_bound_runs_more_tasks`

### Variant Metrics

| task | variant | status | score | pass/total | cost | calls | route | cmds | ref | err probes | stdin | deps | compiles | notes |
|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| `cmatsuoka__figlet.202a0a8` | baseline | Submitted | None | missing | 0.27 | 29 | c->shell | 29 | 18 | 1 | 94 | 0 | 5 | none obvious |
| `cmatsuoka__figlet.202a0a8` | oracle_spec | Submitted | None | missing | 0.71 | 48 | c | 48 | 36 | 7 | 180 | 0 | 6 | none obvious |
| `sirwart__ripsecrets.34c9e03` | baseline | Submitted | 39.61 | 242/611 | 0.30 | 30 | python->rust->python->shell | 30 | 3 | 10 | 31 | 1 | 5 | limited reference probing; tried unavailable external dependencies; implementation started very early |
| `sirwart__ripsecrets.34c9e03` | oracle_spec | Submitted | 72.5 | 443/611 | 0.50 | 49 | python | 49 | 35 | 25 | 41 | 0 | 7 | none obvious |

### Baseline vs Scaffold Interpretation

| task | baseline | scaffold | delta | why baseline failed | why scaffold helped |
|---|---:|---:|---:|---|---|
| `cmatsuoka__figlet.202a0a8` | None | None | None | eval error: missing_eval_json | implementation route changed: c->shell -> c; more reference probing |
| `sirwart__ripsecrets.34c9e03` | 39.61 | 72.5 | 32.89 | spent budget on external dependencies; weak reference executable exploration | score improved by 32.89 points; implementation route changed: python->rust->python->shell -> python; reduced external dependency attempts; more reference probing |
