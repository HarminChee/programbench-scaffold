# yj Generated Oracle v3 Comparison

Date: 2026-07-09

Instance: `sclevine__yj.8016400`
Repository: `sclevine/yj`
Commit: `80164002c0d7f88aa58fa5bec8a8cf4f1bb4e93b`

## Summary

`generated_yj_oracle_v3` is a cleanroom black-box executable oracle bundle. It
does not copy ProgramBench official tests or upstream source files. The
generator copies the ProgramBench cleanroom reference executable, runs a
targeted CLI case matrix against it, and records exact `stdout`, `stderr`, and
exit-code observations as pytest fixtures.

The v3 suite has 85 executed pytest cases and reaches 88.8% Go statement
coverage. This matches the PB official all-active-branch coverage baseline
observed for yj, while using far fewer executed cases than PB official tests
(`825` raw / about `821` active after ProgramBench filtering). Equal statement
coverage does not mean byte-for-byte oracle equivalence; function-level coverage
still differs in a few places.

## Coverage

| suite | source | tests | result | Go statement coverage |
|---|---:|---:|---|---:|
| `PB official all_active` | official PB test blobs | 825 raw / about 821 active | ProgramBench-filtered pass; cleanroom/source/coverage consistent | `88.8%` |
| `native go test` | upstream repo tests | repo-native | pass on clean source checkout | `76.2%` |
| `generated_yj_oracle_v1` | ours, black-box cleanroom capture | 34 | cleanroom/source/coverage pass and consistent | `70.5%` |
| `generated_yj_oracle_v2` | ours, coverage-guided black-box capture | 53 | cleanroom/source/coverage pass and consistent | `78.7%` |
| `generated_yj_oracle_v3` | ours, coverage-guided black-box capture | 85 | cleanroom/source/coverage pass and consistent | `88.8%` |

## v3 Gates

- `reference_pass`: `85/85` pass on cleanroom binary, source-built binary, and Go coverage binary.
- `binary_consistency`: cleanroom/source/coverage JUnit test names and pass/fail counts match.
- `dummy_reject`: `/bin/true` no-output dummy gets `84 failed, 1 passed`, pytest returncode `1`.
- `source_leak_scan`: no `.go`, `go.mod`, `go.sum`, `Makefile`, `Dockerfile`, `.c`, `.h`, or `.rs` files; no matches for source-identifying strings.
- `repeat_check`: with `GOCOVERDIR` set for the coverage binary, the same suite reruns as `85 passed`.

## What Changed From v2 To v3

v3 keeps the v2 cases and adds targeted cases based on function-level coverage
gaps against PB official tests:

- flag validity matrix: split flags, invalid `-k`, `-e`, and `-i` combinations;
- YAML complex map keys, merge sequences, large alias expansion, nulls, and invalid merge errors;
- JSON-to-YAML key parsing for array/object/null/stringified keys and numeric-looking floats;
- TOML special floats, nested arrays of tables, null-handling through TOML output, and many-key structs;
- HCL nested repeated blocks, duplicate attributes, block/scalar conflicts, and invalid HCL parse errors.

The largest v2 gaps that v3 closed include `convert/hcl.String`,
`yaml/json.Marshal`, `toml.Decoder.convert`, `toml.Decoder.keysEqual`,
`order.alphaIndexBuf`, `main.main`, and most flag transform paths.

## Remaining Difference From PB Official

The aggregate statement coverage now matches PB official at `88.8%`, but it is
not identical coverage. PB official still covers a few functions more deeply,
including `order.MapSlice.Merge`, `yaml.Encoder.other`, `toml.Encoder.convert`,
and `yaml.decodeTracker.alias`. v3 covers some other paths more deeply, such as
`main.Run`, `convert.YAML.Encode`, YAML merge error handling, and HCL
`catchFailure`.

So the correct interpretation is: v3 is a strong first reproduction of PB-like
oracle strength for yj under this Go statement-coverage metric, not proof that
we exactly reproduced PB's private oracle-generation process.

## Reproduction Commands

```bash
python3 tools/programbench_generate_cli_oracle_bundle.py sclevine__yj.8016400 \
  --profile yj \
  --suite-label generated_yj_oracle_v3 \
  --output-root reports/programbench_generated_oracles_2026-07-09 \
  --work-root /tmp/programbench_generated_cli_oracles_2026-07-09 \
  --overwrite

python3 tools/programbench_go_coverage_harness.py sclevine__yj.8016400 \
  --tasks-root /home/harminchee/codex-workspaces/ProgramBench/src/programbench/data/tasks \
  --oracle-material-root reports/programbench_generated_oracles_2026-07-09/sclevine__yj.8016400/generated_yj_oracle_v3/oracle_tests \
  --suite-label generated_yj_oracle_v3 \
  --work-root /tmp/programbench_source_coverage_yj_generated_v3 \
  --output-root reports/programbench_source_coverage_2026-07-09 \
  --overwrite \
  --run-native-tests \
  --compare-binaries \
  --xdist 1
```
