# ProgramBench Oracle Reproduction Plan

Last updated: 2026-07-10

## Direction

The immediate goal is ProgramBench parity before external scale-up. The Gym
should first reproduce the ProgramBench task contract, filtering rules,
evaluation reward, and oracle-test construction loop closely enough that we can
run the same machinery on official ProgramBench instances and then extend it to
new repositories.

External GitHub candidates are paused until this path is clear.

## What ProgramBench Does

ProgramBench instances give the agent a cleanroom Docker workspace containing
documentation and an execute-only reference binary. The inference container must
run without internet. The agent submits a complete workspace as
`submission.tar.gz`; evaluation extracts it, runs `./compile.sh`, expects a new
`./executable`, and then runs hidden behavioral tests.

ProgramBench scoring is test-pass fraction after official filtering:

- keep active branches from `tests.json`;
- remove ignored branches and ignored individual tests;
- count missing expected tests as `not_run`;
- score as `passed / total` over the remaining tests.

So our Gym reward must not invent a different primary success metric. Additional
coverage or mutation scores are quality signals for test generation, not a
replacement for the ProgramBench score.

## Oracle-Test Construction Target

The paper describes a four-stage construction flow:

1. identify GitHub repositories that build standalone programs;
2. build the gold executable from source and record reproducible build steps;
3. generate behavioral pytest suites by agent-driven probing, source/test/doc
   inspection, harvesting existing behavioral tests, and iterative line-coverage
   improvement;
4. strip implementation details and build a cleanroom image with only docs,
   the execute-only reference binary, and allowed assets.

The public ProgramBench codebase contains the evaluator, metadata, filters, and
test-blob format. The exact private generation prompts and coverage loop are not
fully released, so our reproduction target is behavioral equivalence: comparable
test strength, coverage, determinism, and dummy rejection rather than byte-for-
byte identical oracle tests.

## Reproduction Quality Gates

An oracle-test reproduction is not accepted until these gates are recorded:

- `official_metadata_loaded`: `task.yaml` and `tests.json` parsed.
- `official_blob_synced`: per-branch archives available locally.
- `cleanroom_image_smoke`: official `task_cleanroom_v6` image pulls and runs
  with `--network none`.
- `reference_pass`: generated or sanitized tests pass on the gold executable.
- `dummy_reject`: tests do not all pass against a dummy candidate.
- `deterministic`: repeated runs produce stable pass/fail outcomes.
- `source_leak_scan`: agent-visible tests do not contain source/build leakage.
- `coverage_measured`: generated tests run against an instrumented original
  source checkout and report source coverage where feasible.
- `coverage_comparable`: coverage is compared with official ProgramBench tests
  and/or native upstream tests.
- `programbench_eval_compatible`: candidate submissions can still be scored by
  ProgramBench-style active-branch / ignored-test filtering.

## Implementation Steps

1. Use official ProgramBench tasks first, starting with
   `sclevine__yj.8016400`, `multiprocessio__dsq.c3ae0ba`, and
   `rs__jplot.2a54bcc`.
2. Sync official blobs with `programbench blob sync <instance_id>` and record
   branch/test counts from `tests.json`.
3. Pull the corresponding `task_cleanroom_v6` image and verify no-network
   execution.
4. Reconstruct the source checkout at the pinned `repository`/`commit` in a
   private workspace.
5. Build a language-specific coverage harness:
   Go via `go test`/coverage where possible, Rust via `cargo llvm-cov` or
   `grcov`, C/C++ via `gcov`/`lcov`, Python via `coverage.py`.
6. Run official ProgramBench branch tests against the instrumented source path
   when possible, producing the official-oracle coverage baseline.
7. Generate candidate oracle tests using the ProgramBench-style loop:
   docs/source/existing-test inspection, black-box probing of the reference
   binary, behavioral pytest assertions, iterative coverage targeting, and
   revision of weak assertions.
8. Filter generated tests by reference pass, dummy reject, determinism,
   offline/no-network behavior, source-leak scan, assertion-strength lint, and
   coverage delta.
9. Report per-instance results in `quality_report.json` plus a human-readable
   status report before marking any instance accepted.

## Environment Evidence From 2026-07-08

- WSL/Linux: Ubuntu 26.04 LTS on WSL2, `x86_64`.
- Docker: server `29.1.3`, `linux/amd64`; Compose `2.40.3`; buildx `0.30.1`.
- Docker no-network check: `alpine:3.20` and ProgramBench cleanroom containers
  both block outbound network with `--network none`.
- ProgramBench CLI: `/home/harminchee/.local/bin/uvx --from programbench programbench --help` works.
- Official blob sync: `programbench blob sync sclevine__yj.8016400` downloaded
  the yj test blobs into the HuggingFace cache.
- Official image smoke: `programbench/sclevine_1776_yj.8016400:task_cleanroom_v6`
  pulled successfully and exposed `/workspace/executable` as execute-only.
- Source-coverage baseline: `tools/programbench_go_coverage_harness.py` ran
  all 9 active yj branches against cleanroom, source-built, and Go coverage
  binaries under `TZ=UTC`. ProgramBench-filtered branch results passed and the
  three binaries were behavior-consistent. Merged official-test Go statement
  coverage is 88.8%; native `go test` statement coverage is 76.2%.
- Generated yj oracle v2: `tools/programbench_generate_cli_oracle_bundle.py`
  generated 53 cleanroom black-box CLI cases for `sclevine__yj.8016400`.
  The same Go coverage harness reports 78.7% Go statement coverage. This is
  above the native baseline by 2.5 percentage points and below the PB official
  all-active-branch baseline by 10.1 percentage points. The v2 suite passes on
  cleanroom/source/coverage binaries, rejects an `exit 0` no-output dummy
  (`52 failed, 1 passed`), passes source-leak scans, and reruns as `53 passed`
  with `GOCOVERDIR` set.
- Generated yj oracle v3: the same generator now produces 85 cleanroom
  black-box CLI cases with coverage-guided additions for flag validation, YAML
  complex keys/alias/merge/null/error paths, TOML special floats/nested arrays
  of tables/many-key structs, and HCL duplicate/repeated/error paths. The Go
  coverage harness reports 88.8% Go statement coverage, matching the PB
  official all-active-branch baseline and exceeding native `go test` by 12.6
  percentage points. The v3 suite passes on cleanroom/source/coverage binaries,
  rejects a no-output `/bin/true` dummy (`84 failed, 1 passed`), passes
  source-leak scans, and reruns as `85 passed` with `GOCOVERDIR` set. Equal
  aggregate statement coverage is not treated as byte-for-byte oracle
  equivalence; function-level coverage still differs in a few paths.

## Go Oracle Framework Evidence From 2026-07-10

The Go path now has a reusable framework with three scripts:

- `tools/programbench_generate_cli_oracle_bundle.py` is a profile/JSON-case
  black-box capture engine. It copies `/workspace/executable` from the
  ProgramBench cleanroom image, runs candidate CLI cases, and writes executable
  pytest oracles with exact returncode/stdout/stderr fixtures.
- `tools/programbench_go_coverage_harness.py` clones the pinned Go source,
  auto-discovers the executable `main` package with `go list -json ./...`,
  builds source and coverage binaries, compares cleanroom/source/coverage
  behavior, and records Go statement coverage plus native `go test` coverage.
- `tools/programbench_run_generated_oracle_quality_gates.py` runs reusable
  generated-oracle gates: dummy rejection, source-leak scan, and repeat
  execution. It creates a pytest venv automatically when `--python` is omitted.

Important harness fix: oracle material copying no longer deletes source
directories that are absent from the oracle bundle. This was found by the
`rs__jplot.2a54bcc` smoke run, where the older harness deleted the repo's
source `data/` package before `go tool cover -func`.

Recorded results:

| instance | suite | cases | Go statement coverage | native coverage | quality gates |
| --- | --- | ---: | ---: | ---: | --- |
| `sclevine__yj.8016400` | `generated_yj_oracle_v4_framework_regression` | 85 | 88.8% | 76.2% | pass |
| `multiprocessio__dsq.c3ae0ba` | `generated_generic_cli_smoke_v1` | 6 | 22.7% | 0.0% | pass |
| `rs__jplot.2a54bcc` | `generated_generic_cli_smoke_v1` | 6 | 10.8% | 0.0% | pass |
| `psampaz__go-mod-outdated.bb79367` | `generated_generic_cli_smoke_v1` | 6 | 34.7% | 84.7% | pass |

The three `generic-cli-smoke` suites deliberately do not inspect official
ProgramBench oracle tests. They validate framework generality, not oracle
strength. The next quality step is to feed repo-aware or agent-generated
candidate cases through `--cases-json` and require coverage lift under the same
reference-pass, dummy-reject, repeat, source-leak, and binary-consistency gates.

Detailed evidence is in
`reports/programbench_go_oracle_framework_status_2026-07-10.md`.
