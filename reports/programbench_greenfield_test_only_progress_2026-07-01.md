# ProgramBench Greenfield Test-Only Progress

Date: 2026-07-01

## What We Did

This update follows Robin's latest direction:

> First understand ProgramBench deeply, then implement a test-only spec setting
> where the agent gets docs plus executable oracle tests in a no-internet
> container, framed as greenfield implementation rather than reverse
> engineering.

Completed in this pass:

1. Re-read the original ProgramBench and mini-swe-agent workflow.
2. Selected a 10-project dev set with a language distribution close to the
   original benchmark.
3. Implemented a sanitizer that turns official ProgramBench test blobs into
   agent-visible executable test bundles without copying upstream source code.
4. Generated full sanitized bundles for the 5 dev-set tasks whose test blobs
   are already cached locally.
5. Added a greenfield mini-swe-agent prompt/config override.
6. Added a local runner wrapper that injects sanitized tests before coding.
7. Dry-ran the runner on `yj` to verify config loading, bundle discovery, and
   metadata generation without spending model budget.

## Key Code/Artifact Outputs

| path | purpose |
| --- | --- |
| `docs/programbench_codebase_notes.md` | Notes on original ProgramBench inference/eval workflow and where to hook test-only injection. |
| `configs/programbench_devset_10.json` | Machine-readable 10-project dev set. |
| `docs/programbench_devset_10.md` | Human-readable dev set rationale. |
| `tools/programbench_build_test_bundle.py` | Builds sanitized executable-test bundles from official branch tarballs. |
| `configs/programbench_greenfield_test_only.yaml` | Prompt override for greenfield docs + executable tests setting. |
| `tools/programbench_run_greenfield_tests.py` | Runner wrapper that injects `oracle_tests` into the cleanroom container before `agent.run()`. |
| `reports/test_bundles/` | Generated sanitized bundles for locally cached tasks. |
| `reports/programbench_greenfield_dryrun/run_metadata.json` | Dry-run metadata confirming the runner can load yj with test injection enabled. |

## Dev Set

Original ProgramBench language distribution:

| language | count | share |
| --- | ---: | ---: |
| Rust | 107 / 201 | 53.2% |
| Go | 46 / 201 | 22.9% |
| C | 33 / 201 | 16.4% |
| C++ | 12 / 201 | 6.0% |
| other | 3 / 201 | 1.5% |

Selected dev-set distribution:

| language | count | share |
| --- | ---: | ---: |
| Rust | 5 / 10 | 50% |
| Go | 3 / 10 | 30% |
| C | 2 / 10 | 20% |

Main selected tasks:

| task | language | active tests | branches |
| --- | --- | ---: | ---: |
| `sirwart__ripsecrets.34c9e03` | Rust | 611 | 10 |
| `wfxr__csview.8ac4de0` | Rust | 335 | 7 |
| `wfxr__code-minimap.0ddeea5` | Rust | 313 | 8 |
| `clog-tool__clog-cli.7066cba` | Rust | 575 | 10 |
| `drew-alleman__datasurgeon.d257cee` | Rust | 502 | 8 |
| `sclevine__yj.8016400` | Go | 767 | 9 |
| `multiprocessio__dsq.c3ae0ba` | Go | 542 | 10 |
| `rs__jplot.2a54bcc` | Go | 583 | 8 |
| `cmatsuoka__figlet.202a0a8` | C | 872 | 12 |
| `cslarsen__jp2a.61d205f` | C | 631 | 11 |

## Important Finding

Official ProgramBench branch tarballs are not safe to give directly to the
agent. They often include upstream source code.

Concrete examples:

- `yj` branch tar contains `main.go`, `flags.go`, `convert/*.go`,
  `yaml/*.go`, `toml/*.go`, etc.
- `dsq` branch tar contains `main.go`, `sqlite.go`, `go.mod`, `go.sum`, etc.

So the test-only upper-bound setting must not copy raw `<branch>.tar.gz` files
into the agent workspace. We now build sanitized bundles instead.

The sanitizer keeps:

- `eval/tests/**`
- `eval/run.sh`
- `testdata/**`
- `fixtures/**`, `examples/**`, `samples/**`, `resources/**`, `assets/**`

The sanitizer excludes:

- root/project source files such as `.go`, `.rs`, `.c`, `.h`, `.cpp`
- build metadata such as `go.mod`, `Cargo.toml`, `Makefile`, `build.sh`
- broad project directories outside test/fixture paths

During audit, the first sanitizer version accidentally allowed generic `data/`,
which let `jplot` source files like `data/spec.go` into the bundle. We caught
that with a source-leak scan, removed generic `data/` from the allowlist, and
regenerated bundles. The final scan found zero suspicious source/build files in
the generated cached-task bundles.

## Sanitized Bundle Stats

Generated full bundles for the 5 dev-set tasks whose blobs are cached locally:

| task | branches | included files | excluded files |
| --- | ---: | ---: | ---: |
| `sirwart__ripsecrets.34c9e03` | 10 | 339 | 640 |
| `sclevine__yj.8016400` | 9 | 580 | 317 |
| `multiprocessio__dsq.c3ae0ba` | 10 | 439 | 363 |
| `rs__jplot.2a54bcc` | 8 | 139 | 309 |
| `cmatsuoka__figlet.202a0a8` | 12 | 1077 | 1116 |

Cached locally:

- `sirwart__ripsecrets.34c9e03`
- `sclevine__yj.8016400`
- `multiprocessio__dsq.c3ae0ba`
- `rs__jplot.2a54bcc`
- `cmatsuoka__figlet.202a0a8`

Missing local blobs for now:

- `wfxr__csview.8ac4de0`
- `wfxr__code-minimap.0ddeea5`
- `clog-tool__clog-cli.7066cba`
- `drew-alleman__datasurgeon.d257cee`
- `cslarsen__jp2a.61d205f`

These can be synced later with `programbench blob sync <instance_id>` on a
networked machine.

## Runner Dry-Run Result

Dry-run command:

```bash
uv run --with mini-swe-agent --with boto3 python \
  /Users/harmin/Desktop/programbench/tools/programbench_run_greenfield_tests.py \
  --workspace-root /Users/harmin/Desktop/programbench \
  --task sclevine__yj.8016400 \
  --output reports/programbench_greenfield_dryrun \
  --dry-run
```

Result:

- loaded ProgramBench and mini-swe-agent successfully
- selected `sclevine__yj.8016400`
- found `reports/test_bundles/sclevine__yj.8016400/oracle_tests`
- resolved config merge order:
  1. mini-swe-agent built-in `programbench.yaml`
  2. `configs/programbench_greenfield_test_only.yaml`
  3. `configs/programbench_mac_smoke.yaml`
- set `hide_reference_executable: true`
- wrote `reports/programbench_greenfield_dryrun/run_metadata.json`
- did not run the agent or spend model budget

The runner now also requires `--yes-run-agent` for non-dry-run execution, so a
real model run cannot be triggered accidentally.

## Current Status

The core interface Robin asked for is now implemented at MVP level:

```text
official ProgramBench test blobs
-> sanitized executable test bundle
-> no-internet ProgramBench cleanroom container
-> greenfield mini-swe-agent prompt
-> agent sees docs + ./oracle_tests before coding
-> unchanged programbench eval after submission
```

This corrects the previous weaker pipeline:

```text
tests.json metadata
-> compact markdown/spec summary
-> agent prompt injection
```

The old summary-based approach can still be useful later, but the current
upper-bound experiment now uses executable tests directly.

## Actual Runs Completed

Three real model runs were completed with the fixed greenfield test-only runner.

| task | agent status | cost | test-only score | baseline score | previous compact spec/plan score |
| --- | --- | ---: | ---: | ---: | ---: |
| `sclevine__yj.8016400` | `LimitsExceeded` | `$0.2726` | 246 / 767 = 32.07% | 242 / 767 = 31.55% | 405 / 767 = 52.80% |
| `multiprocessio__dsq.c3ae0ba` | `Submitted` | `$0.2395` | 301 / 542 = 55.54% | 0 / 542 = 0.00% | 337 / 542 = 62.18% |
| `sirwart__ripsecrets.34c9e03` | `LimitsExceeded` | `$0.3019` | 408 / 611 = 66.78% | 242 / 611 = 39.61% | 443 / 611 = 72.50% |

Detailed reports:

- `reports/programbench_greenfield_test_only_yj_run_2026-07-01.md`
- `reports/programbench_greenfield_test_only_dsq_run_2026-07-01.md`
- `reports/programbench_greenfield_test_only_ripsecrets_run_2026-07-01.md`

Important correction during the yj run:

- The first yj eval was contaminated because `oracle_tests` remained in
  `submission.tar.gz`.
- Official pytest collected injected tests and produced unexpected-test
  warnings.
- The runner now excludes `oracle_tests` and `.git` from submissions.
- yj was re-evaluated with a cleaned submission and no warnings.

Current interpretation:

- `yj`: raw executable tests alone did not help much under the same tight
  budget; it improved some YAML/error behavior but regressed TOML/order behavior.
- `dsq`: raw executable tests gave a strong upper-bound signal, turning a
  compile-failed baseline into a 55.54% pass rate.
- `ripsecrets`: raw executable tests also gave a strong signal, improving from
  39.61% to 66.78%, but still below the compact spec/plan score.
- Across these tasks, the previous compact spec + architecture plan remains
  stronger than raw executable tests alone. This suggests our scaffold should
  combine executable tests with test triage, planning, and exact CLI/error
  prioritization.

## Next Actions

1. Run `jplot` only after deciding whether its terminal/time behavior is worth
   the Mac-local cost.
2. Run `figlet` as the next cached C case if we want one more local task before
   moving to a server.
3. Sync the missing 5 dev-set blobs and extend the experiment to all 10 tasks.
4. Add automatic comparison/report generation for baseline vs test-only vs
   compact spec/plan.
5. Initialize/push the GitHub repo and split this work into small reviewable
   commits/PRs.

## Open Risks

- Mac Docker/amd64 emulation is still not reliable for full official
  evaluation; Linux x86-64 remains the proper evaluation environment.
- Some sanitized test branches may need task-specific fixture rules.
- The greenfield runner currently hides `./executable` by default; we should
  keep this unless Robin explicitly wants reference-binary access retained.
- This local directory is not currently a git repository, so repo creation and
  push still need to be done before code review with Robin.
