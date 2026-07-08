# yj Generated Oracle Comparison

Date: 2026-07-08

Instance: `sclevine__yj.8016400`
Repository: `sclevine/yj`
Commit: `80164002c0d7f88aa58fa5bec8a8cf4f1bb4e93b`

## Summary

We generated our own executable pytest oracle bundle from cleanroom-visible
behavior, then ran it through the same Go coverage harness used for the PB
official baseline. This is not a copy of PB official tests. The generator copies
the cleanroom reference executable, runs README-derived CLI cases against it,
and stores exact stdout, stderr, and exit-code observations as pytest fixtures.

The improved `generated_yj_oracle_v2` suite reaches `78.7%` Go statement
coverage. That is above the upstream native `go test` baseline (`76.2%`) and
below PB official all-active-branch tests (`88.8%`) by `10.1` percentage
points.

## Coverage

| suite | source | tests | result | Go statement coverage |
|---|---:|---:|---|---:|
| `PB official all_active` | official PB test blobs | 825 raw / 821 active | ProgramBench-filtered pass; cleanroom/source/coverage consistent | `88.8%` |
| `native go test` | upstream repo tests | repo-native | pass on clean source checkout | `76.2%` |
| `generated_yj_oracle_v1` | ours, black-box cleanroom capture | 34 | cleanroom/source/coverage pass and consistent | `70.5%` |
| `generated_yj_oracle_v2` | ours, black-box cleanroom capture plus coverage-guided case expansion | 53 | cleanroom/source/coverage pass and consistent | `78.7%` |

## v2 Gates

- `reference_pass`: `53/53` pass on cleanroom binary, source-built binary, and Go coverage binary.
- `binary_consistency`: cleanroom/source/coverage JUnit test names and pass/fail counts match.
- `dummy_reject`: `exit 0` no-output dummy gets `52 failed, 1 passed`, pytest returncode `1`.
- `source_leak_scan`: no `.go`, `go.mod`, `go.sum`, `Makefile`, `Dockerfile`, `.c`, `.h`, or `.rs` files; no matches for source-identifying strings.
- `repeat_check`: with `GOCOVERDIR` set for the coverage binary, the same suite reruns as `53 passed`.

## What Changed From v1 To v2

v1 covered the basic README conversion surface: help/version, invalid flags,
YAML/JSON/TOML/HCL conversions, HTML escaping, indentation, float conversion,
parse-key behavior, empty input, and invalid input.

v2 added coverage-guided cases for short alias flags (`-y`, `-t`, `-r`, `-c`),
no-dash flag forms, YAML merge/alias/tag inputs, JSON root arrays, TOML arrays
of tables, TOML dotted keys, and HCL repeated blocks. That moved coverage from
`70.5%` to `78.7%`.

## Remaining Gap

The main remaining gap to PB official coverage is not the simple format matrix;
it is deeper edge behavior. Per-function coverage still lags PB official around
some YAML alias/merge/tag paths, TOML conversion edge cases, HCL normalization,
and several panic/error recovery paths. Closing the last `10.1` percentage
points likely needs the next PB-like step: inspect upstream native tests/source
for feature inventory, generate targeted black-box cases from those features,
then keep only cases that pass reference, reject dummy, avoid leakage, and
increase coverage.

## Reproduction Commands

```bash
python3 tools/programbench_generate_cli_oracle_bundle.py sclevine__yj.8016400 \
  --profile yj \
  --suite-label generated_yj_oracle_v2 \
  --output-root reports/programbench_generated_oracles_2026-07-08 \
  --work-root /tmp/programbench_generated_cli_oracles_2026-07-08 \
  --overwrite

python3 tools/programbench_go_coverage_harness.py sclevine__yj.8016400 \
  --tasks-root /home/harminchee/codex-workspaces/ProgramBench/src/programbench/data/tasks \
  --oracle-material-root reports/programbench_generated_oracles_2026-07-08/sclevine__yj.8016400/generated_yj_oracle_v2/oracle_tests \
  --suite-label generated_yj_oracle_v2 \
  --work-root /tmp/programbench_source_coverage_yj_generated \
  --output-root reports/programbench_source_coverage_2026-07-08 \
  --overwrite \
  --run-native-tests \
  --compare-binaries \
  --xdist 1
```
