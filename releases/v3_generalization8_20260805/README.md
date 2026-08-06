# V3 Generalization-8 Oracle Gym Release

This release contains the repository-agnostic V3 workflow improvements and the
auditable results for the eight-repository cross-language rerun completed on
2026-08-05. The package is intentionally separate from the older V2/V3
published suites.

## Contents

```text
workflow/
  shared_tools/       shared gold-capture, lifecycle, and coverage harnesses
  v3_tools/           planning, batching, materialization, filtering, gates
  crosslang_tools/    resumable refinement and monotonic suite promotion
  tests/              focused regression tests for the new mechanisms
configs/              cohort and coverage configuration
generated_tests/      one tar.gz pytest oracle bundle per rerun repository
results/
  results_audit_20260805.json
  generalization8_results.md
  previous_v3_12_repo_results.md
  previous_crosslang20_20_repo_results.json
```

Each generated-suite archive contains the pytest adapter, manifest, and
fixtures. Build binaries, Docker layers, model responses, API keys, and
machine-specific logs are deliberately excluded. Extract archives with
`tar -xzf <repo>.tar.gz`.

## What changed in this V3 release

The workflow adds a persistent multi-step sequence DSL for servers, file
watchers, PTY programs, and stateful CLIs; cleanroom dynamic-library discovery;
portable C/C++ build and gcov retries; adaptive behavior-group retention; final
post-repair recapture; and a monotonic promotion gate. A refinement round is
promoted only when it passes the quality gates and does not regress either
source-coverage metric. Otherwise the previously accepted suite is inherited.
No repository name or repository-specific constant is used by these mechanisms.

PB official tests and PB coverage are holdout baselines. They are not included
in the generation context and are only used for the post-generation comparison
tables.

## Coverage labels

* C/C++: executable line / branch coverage from gcov/gcovr.
* Rust: LLVM line / region coverage.
* Go: executable line / statement coverage in the existing V3 Go release.

Coverage is source-mapped evidence. QEMU/Callgrind/AFL-like signals, when
collected by the parent V3 pipeline, are auxiliary dynamic-reachability
signals and are not substituted for source coverage.

## Reproduce a local smoke test

Run from the repository root in WSL after installing the ProgramBench Python
environment:

```bash
wsl.exe -d ProgramBench-Ubuntu-22.04 -- \
  /home/programbench/research/programbench-scaffold/.venv/bin/python -m pytest -q \
  releases/v3_generalization8_20260805/workflow/tests/test_v3_monotonic_promotion.py
```

For a full experiment, use the supplied cohort with
`workflow/crosslang_tools/run_multiround_refinement.ps1`. It expects the
existing ProgramBench cleanroom/source workspaces and an already configured
Agent Maestro endpoint. The controller uses Opus for semantic refinement,
writes restartable round directories, and never needs a newly generated API
key. Do not put credentials in files or logs.

The promotion policy can also be run independently:

```bash
python workflow/crosslang_tools/promote_best_refinement_v3.py \
  --prior-root <prior-root> --candidate-root <candidate-root> \
  --output-root <promoted-root> --output-summary <promotion-summary.json>
```

## Result interpretation

`results/generalization8_results.md` reports both the latest candidate and the
suite actually selected by the non-regression policy. `entr` and `lnav` show
why this distinction matters: their latest candidates had lower coverage, so
the prior accepted suites remain the effective results. GDAL is now measurable
but still blocked by source/cleanroom driver and data parity; it is not claimed
as a high-quality success.

The previous 12-repository comparison is preserved in
`results/previous_v3_12_repo_results.md`; the complete older 20-repository
machine-readable snapshot is `results/previous_crosslang20_20_repo_results.json`.
