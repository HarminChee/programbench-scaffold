# ProgramBench Greenfield Test-Only 10-Task Summary

## Setting

We evaluated a greenfield implementation setting on a 10-task dev set. The agent received the original cleanroom documentation plus sanitized executable oracle tests under `./oracle_tests`. It did not receive original source code, raw `tests.json` metadata as the primary interface, internet access, or the reference executable.

This is an upper-bound experiment: it asks how much direct executable tests/specs can help before we invest in automatically mining comparable specs from black-box behavior.

## Aggregate

- Tasks completed with official eval: 10/10
- Average test-only score: 40.78%
- Average model cost per task: $0.284
- Agent exit statuses: Submitted=5, LimitsExceeded=5

## Result Table

| task | lang | status | cost | pass/total | score | baseline if available | compact-spec if available | delta vs baseline | route | probes/deps | failure/effect hypothesis |
|---|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|
| `sirwart__ripsecrets.34c9e03` | rs | LimitsExceeded | $0.302 | 408/611 | 66.78% | 242/611 (39.61%) | 443/611 (72.50%) | +27.17 | rust->shell->python->shell | ref 3, err 10, stdin 19, deps 1 | budget/status=LimitsExceeded; external dependency attempts; test scaffold exposed useful behavior |
| `wfxr__csview.8ac4de0` | rs | Submitted | $0.309 | 258/335 | 77.01% | - | - | - | rust->shell->python->shell->python | ref 4, err 18, stdin 42, deps 3 | external dependency attempts; test scaffold exposed useful behavior |
| `wfxr__code-minimap.0ddeea5` | rs | LimitsExceeded | $0.316 | 73/313 | 23.32% | - | - | - | python | ref 4, err 34, stdin 26, deps 0 | budget/status=LimitsExceeded; complex hidden behavior remains uncovered |
| `clog-tool__clog-cli.7066cba` | rs | LimitsExceeded | $0.312 | 97/575 | 16.87% | - | - | - | rust->shell->rust | ref 0, err 25, stdin 76, deps 2 | budget/status=LimitsExceeded; external dependency attempts; complex hidden behavior remains uncovered |
| `drew-alleman__datasurgeon.d257cee` | rs | LimitsExceeded | $0.303 | 184/502 | 36.65% | - | - | - | python | ref 2, err 21, stdin 5, deps 0 | budget/status=LimitsExceeded; partial CLI/core behavior only |
| `sclevine__yj.8016400` | go | LimitsExceeded | $0.273 | 246/767 | 32.07% | 242/767 (31.55%) | 405/767 (52.80%) | +0.52 | go->shell | ref 2, err 47, stdin 15, deps 11 | budget/status=LimitsExceeded; external dependency attempts; partial CLI/core behavior only |
| `multiprocessio__dsq.c3ae0ba` | go | Submitted | $0.240 | 301/542 | 55.54% | 0/542 (0.00%) | 337/542 (62.18%) | +55.54 | python->shell->python | ref 6, err 8, stdin 4, deps 1 | external dependency attempts; test scaffold exposed useful behavior |
| `rs__jplot.2a54bcc` | go | Submitted | $0.291 | 401/583 | 68.78% | - | 116/583 (19.90%) | - | go->shell->go | ref 2, err 7, stdin 13, deps 0 | test scaffold exposed useful behavior |
| `cmatsuoka__figlet.202a0a8` | c | Submitted | $0.246 | 144/872 | 16.51% | - | - | - | c->shell | ref 2, err 7, stdin 46, deps 0 | complex hidden behavior remains uncovered |
| `cslarsen__jp2a.61d205f` | c | Submitted | $0.249 | 90/631 | 14.26% | - | - | - | c->shell | ref 3, err 6, stdin 72, deps 2 | external dependency attempts; complex hidden behavior remains uncovered |

## Main Observations

1. The test-only setting is now fully exercised across the 10-task dev set. This satisfies the current experimental TODO at MVP scale: docs plus executable tests were injected into no-internet cleanroom containers and evaluated with official ProgramBench eval.
2. The signal is mixed but useful. Test-only specs strongly help several tasks with concrete CLI/test behavior (`ripsecrets`, `dsq`, `jplot`, `csview`), but are weak for tasks requiring large compatibility surfaces or image/font-heavy behavior (`figlet`, `jp2a`, `clog-cli`, `code-minimap`).
3. Compared with earlier compact-spec/architecture-plan runs where available, raw executable tests are often weaker than a compact behavioral summary. This supports the research direction: the scaffold should not merely dump tests, but should mine, summarize, prioritize, and plan from them.
4. Several runs hit `LimitsExceeded`, so budget pressure is a real part of the failure mode. The agent often spends many steps reading tests or implementing broad features instead of prioritizing high-coverage behavioral clusters.

## Trajectory Notes

| task | commands | compiles | notes |
|---|---:|---:|---|
| `sirwart__ripsecrets.34c9e03` | 21 | 4 | limited reference probing; tried unavailable external dependencies; implementation started very early |
| `wfxr__csview.8ac4de0` | 29 | 2 | limited reference probing; tried unavailable external dependencies; implementation started very early |
| `wfxr__code-minimap.0ddeea5` | 25 | 3 | limited reference probing; implementation started very early |
| `clog-tool__clog-cli.7066cba` | 22 | 2 | limited reference probing; tried unavailable external dependencies; implementation started very early |
| `drew-alleman__datasurgeon.d257cee` | 26 | 2 | limited reference probing; implementation started very early |
| `sclevine__yj.8016400` | 29 | 7 | limited reference probing; tried unavailable external dependencies; implementation started very early |
| `multiprocessio__dsq.c3ae0ba` | 29 | 2 | tried unavailable external dependencies; implementation started very early |
| `rs__jplot.2a54bcc` | 29 | 8 | limited reference probing; implementation started very early |
| `cmatsuoka__figlet.202a0a8` | 29 | 3 | limited reference probing; implementation started very early |
| `cslarsen__jp2a.61d205f` | 29 | 1 | limited reference probing; tried unavailable external dependencies; implementation started very early |

## Implementation/Eval Caveats

- We fixed the runner so `oracle_tests` and `.git` are excluded from `submission.tar.gz`; all final submissions were checked clean.
- We sanitized official test bundles to avoid source leakage from raw ProgramBench branch tarballs. Two leakage classes were found and fixed: generic `data/` source files and source-like files under `examples/`.
- `csview` printed branch-level `results_read_failed` warnings during CLI eval, although the official parsed result file produced a normal score after filtering. Keep this task marked as having Mac/Docker eval instability.
- The first `code-minimap` eval attempt failed because Docker Desktop was down; it was rerun after restarting Docker, and the rerun result is the one reported here.
- The first `clog-cli` agent attempt ended in Bedrock/API connection error; it was backed up and rerun. The reported `clog-cli` result is the second normal `LimitsExceeded` run.

## Next Research Step

Use this 10-task result as the dev-set baseline for a better scaffold: executable-test triage, behavior-cluster summarization, and implementation planning. The target is to approach the compact-spec/architecture-plan upper bound without using oracle test metadata directly as final unfair input.
