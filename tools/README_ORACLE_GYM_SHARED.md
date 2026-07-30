# Shared Oracle Gym Tools

V2 and V3 keep their orchestration logic in `v2/tools/` and `v3/tools/`, while
the following repository-level tools implement the common execution layer:

- `programbench_generate_cli_oracle_bundle.py`: executes candidate fixture DSL
  against the cleanroom reference binary, performs determinism capture, and
  writes the pytest behavioral oracle bundle.
- `programbench_go_coverage_harness.py`: builds/runs Go coverage binaries,
  records statement and executable-line coverage, and compares cleanroom,
  source-built, and coverage-built execution.
- `programbench_assertion_linter.py`: flags missing and weak assertions.
- `programbench_run_generated_oracle_quality_gates.py`: repeat execution, dummy
  rejection, source-leak scan, and combined quality reporting.
- `programbench_generate_source_aware_cli_cases.py`: shared source-aware
  candidate utilities retained for compatibility with the pipeline wrapper.
- `programbench_run_pb_style_go_oracle_pipeline.py`: the original shared Go
  pipeline controller used by V2 compatibility paths.
- `programbench_native_coverage_harness.py`: measures native repository tests
  using the same source-mapped reporting conventions.
- `package_oracle_gym_release.py`: creates deterministic publication archives,
  copies browsable manifests/evidence, and records SHA-256 values.
- `verify_oracle_gym_release.py`: checks archive integrity, hashes, manifest
  counts, ten-repository completeness, and report/publication consistency.

Version-specific README files identify the exact order in which these shared
components are used.
