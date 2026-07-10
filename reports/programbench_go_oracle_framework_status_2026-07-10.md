# ProgramBench Go Oracle Framework Status - 2026-07-10

## Summary

The Go oracle-reproduction path is now a reusable framework rather than a
single `yj` script path. The current implementation has three pieces:

- `tools/programbench_generate_cli_oracle_bundle.py`: copies the ProgramBench
  cleanroom reference executable, runs candidate CLI cases against it, and
  materializes exact returncode/stdout/stderr fixtures plus a generated pytest
  harness. It supports built-in profiles and external JSON case specs via
  `--cases-json`.
- `tools/programbench_go_coverage_harness.py`: clones the pinned Go source,
  auto-discovers a `main` package with `go list -json ./...`, builds source and
  coverage binaries, runs oracle tests against cleanroom/source/coverage
  binaries, records native `go test` coverage, and reports Go statement
  coverage.
- `tools/programbench_run_generated_oracle_quality_gates.py`: runs reusable
  oracle quality gates: dummy rejection, source-leak scan, and repeat execution
  against a reference or coverage binary. It now creates its own pytest venv by
  default.

The mature `yj` profile remains the high-coverage, repo-aware case suite. The
new `generic-cli-smoke` profile is intentionally small and repo-agnostic. Its
purpose is to verify that the framework generalizes across ProgramBench Go
instances without reading official oracle tests; high coverage should come from
later repo-aware/agent-generated case specs, not from this smoke profile.

## Framework Fixes

- The generator now writes `eval/generated_cli_manifest.json` and a generic
  `eval/tests/test_generated_cli_oracle.py`; `generated_yj_manifest.json`
  remains only for backwards compatibility on the `yj` profile.
- The generator now supports `--cases-json`, so future README parsers,
  sampling loops, or agents can feed candidate cases into the same black-box
  capture engine.
- The generator records candidate count, skipped timeout count, fixture
  subdirectory, case source, and optional case-spec metadata.
- The coverage harness no longer deletes every possible oracle-material
  directory before copying test material. It only overwrites paths that are
  present in the oracle bundle. This fixed a real bug found on
  `rs__jplot.2a54bcc`, where the old harness deleted the source repo's
  `data/` package before `go tool cover -func`.
- The coverage harness now records `go_build_package`, selected build target,
  candidate `main` packages, `go_coverpkg`, and coverage percent source.
- The coverage harness can fall back to statement-block parsing of a cover
  profile if `go tool cover -func` cannot produce a total.
- The quality gate runner now auto-creates a pytest venv unless `--python` is
  explicitly supplied.

## Verification Matrix

All rows below used Linux x86-64 Docker cleanroom images and did not inspect
ProgramBench official oracle tests for the three `generic-cli-smoke` instances.

| instance | suite | cases | generated Go statement coverage | native Go statement coverage | build target | binary behavior consistent | dummy reject | leak scan | repeat |
| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| `sclevine__yj.8016400` | `generated_yj_oracle_v4_framework_regression` | 85 | 88.8% | 76.2% | `.` | true | true | true | 85 passed |
| `multiprocessio__dsq.c3ae0ba` | `generated_generic_cli_smoke_v1` | 6 | 22.7% | 0.0% | `.` | true | true | true | 6 passed |
| `rs__jplot.2a54bcc` | `generated_generic_cli_smoke_v1` | 6 | 10.8% | 0.0% | `.` | true | true | true | 6 passed |
| `psampaz__go-mod-outdated.bb79367` | `generated_generic_cli_smoke_v1` | 6 | 34.7% | 84.7% | `.` | true | true | true | 6 passed |

`rs__jplot.2a54bcc` discovered two Go `main` packages; the auto selector chose
the root `.` package with score 100. This matched the cleanroom binary behavior
for the generated smoke suite.

## Evidence Paths

- Generated bundles:
  `reports/programbench_generated_oracles_2026-07-09-framework/<instance>/<suite>/oracle_tests`
- Generation reports:
  `reports/programbench_generated_oracles_2026-07-09-framework/<instance>/<suite>/quality_report.json`
- Quality reports:
  `reports/programbench_generated_oracles_2026-07-09-framework/<instance>/<suite>/evaluation_quality_report.json`
- Coverage summaries:
  `reports/programbench_source_coverage_2026-07-09-framework/<instance>/<suite>.go_coverage_summary.json`
  and `.md`

## Commands

Generate a repo-agnostic smoke oracle bundle:

```bash
python3 tools/programbench_generate_cli_oracle_bundle.py multiprocessio__dsq.c3ae0ba \
  --profile generic-cli-smoke \
  --suite-label generated_generic_cli_smoke_v1 \
  --output-root reports/programbench_generated_oracles_2026-07-09-framework \
  --work-root /tmp/programbench_generated_cli_oracles_2026-07-09-framework \
  --overwrite
```

Run Go coverage and binary-consistency checks:

```bash
python3 tools/programbench_go_coverage_harness.py multiprocessio__dsq.c3ae0ba \
  --tasks-root /home/harminchee/codex-workspaces/ProgramBench/src/programbench/data/tasks \
  --oracle-material-root reports/programbench_generated_oracles_2026-07-09-framework/multiprocessio__dsq.c3ae0ba/generated_generic_cli_smoke_v1/oracle_tests \
  --suite-label generated_generic_cli_smoke_v1 \
  --work-root /tmp/programbench_source_coverage_go_generic_smoke \
  --output-root reports/programbench_source_coverage_2026-07-09-framework \
  --overwrite \
  --run-native-tests \
  --compare-binaries \
  --xdist 1 \
  --pytest-timeout 900
```

Run generated oracle quality gates:

```bash
python3 tools/programbench_run_generated_oracle_quality_gates.py \
  --oracle-material-root reports/programbench_generated_oracles_2026-07-09-framework/multiprocessio__dsq.c3ae0ba/generated_generic_cli_smoke_v1/oracle_tests \
  --output-json reports/programbench_generated_oracles_2026-07-09-framework/multiprocessio__dsq.c3ae0ba/generated_generic_cli_smoke_v1/evaluation_quality_report.json \
  --work-root /tmp/programbench_generated_oracle_quality_gates_framework/multiprocessio__dsq.c3ae0ba \
  --repeat-executable /tmp/programbench_source_coverage_go_generic_smoke/multiprocessio__dsq.c3ae0ba/generated_generic_cli_smoke_v1/executable_coverage \
  --repeat-gocoverdir /tmp/programbench_generated_oracle_quality_gates_framework/multiprocessio__dsq.c3ae0ba/repeat_cov \
  --overwrite
```

## Interpretation

The `yj` profile shows that a repo-aware, coverage-guided black-box capture
suite can match the official ProgramBench all-active-branch Go statement
coverage baseline for this instance. This is not evidence that the generated
tests are byte-for-byte equivalent to ProgramBench official oracle tests.

The three `generic-cli-smoke` runs show that the framework can generate,
evaluate, and quality-gate executable oracle bundles on other official Go
instances without reading their official oracle tests. Their coverage is low by
design because the profile only probes no-arg/help/version/invalid-flag
behavior. The next step is to plug a README/source-aware candidate generator or
agent into `--cases-json` and measure coverage lift under the same gates.
