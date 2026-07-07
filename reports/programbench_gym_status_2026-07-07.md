# ProgramBench Gym Status

Date: 2026-07-07

## Completed Scope

This pass implemented Step 0 through Step 4 of the ProgramBench Gym plan.
The workspace was then pruned so old generated reports and obsolete exploratory
scripts no longer dominate the working tree.

## Step 0: Schema

Added `docs/programbench_gym.md`.

The Gym instance schema now standardizes:

- `metadata.json`
- `cleanroom/`
- `private/`
- `oracle_tests/sanitized/`
- `eval/`
- `binary_to_test_sample.json`
- `quality_report.json`

The schema separates private generation artifacts from agent/reward-facing
artifacts, which is the key boundary for avoiding source leakage.

## Step 1-2: MVP-3 Builder

Added `tools/programbench_build_gym_instances.py`.

Generated MVP-3 instances:

- `sclevine__yj.8016400`
- `sirwart__ripsecrets.34c9e03`
- `cmatsuoka__figlet.202a0a8`

Result:

- Static gates passed: 3/3
- Dynamic gates ready: 0/3

Dynamic gates are intentionally skipped on this local Mac run because
ProgramBench Docker images target Linux x86-64.

## Step 3: 10-Task Dev Set

Generated all 10 local dev-set instances:

- `sirwart__ripsecrets.34c9e03`
- `wfxr__csview.8ac4de0`
- `wfxr__code-minimap.0ddeea5`
- `clog-tool__clog-cli.7066cba`
- `drew-alleman__datasurgeon.d257cee`
- `sclevine__yj.8016400`
- `multiprocessio__dsq.c3ae0ba`
- `rs__jplot.2a54bcc`
- `cmatsuoka__figlet.202a0a8`
- `cslarsen__jp2a.61d205f`

Result:

- Static gates passed: 10/10
- Dynamic gates ready: 0/10

## Step 4: Scale Seed Repos

Added `tools/programbench_select_gym_seed_repos.py`.

Generated a 20-repo first-wave GitHub candidate list with target language
distribution:

- Rust: 10
- Go: 6
- C: 2
- C++: 2

These repos are not accepted Gym instances yet. They are candidates that must
pass clone, build, test, dummy, offline, leak-scan, and license gates.

After cleanup, the candidate report was regenerated as:

- `reports/programbench_gym_scale_seed_repos_current.json`
- `reports/programbench_gym_scale_seed_repos_current.md`

Added `configs/programbench_gym_external_pilot5.json` and selected:

- `TomWright/dasel`
- `medialab/xan`
- `pamburus/hl`
- `davidesantangelo/krep`
- `ARM-software/astc-encoder`

Added `tools/programbench_build_github_candidate_instances.py` and generated
candidate-only skeletons:

- `reports/programbench_gym_external_pilot5_candidates`

Result:

- Candidate static gates passed: 5/5
- Accepted training instances: 0/5

## Cleanup

Removed old generated outputs from `reports/`, including prior upper-bound,
fair-spec, greenfield, debug, and generated Gym output directories. The retained
report-side artifact is:

- `reports/test_bundles_oracle_guarded_2026-07-07`

That directory is the sanitized oracle-bundle cache used by the current Gym
builder and is ignored by git.

Also removed untracked old exploratory configs/scripts that are not part of the
current Gym path.

## GitHub Actions Dynamic Gates

Added:

- `.github/workflows/programbench-gym-dynamic-gates.yml`
- `tools/programbench_run_gym_dynamic_gates.py`

The workflow runs on `ubuntu-latest`, syncs ProgramBench blobs, builds missing
source-leak-guarded oracle bundles, materializes cleanroom binaries from Docker,
and records:

- `reference_binary_materialized`
- `reference_smoke_runs`
- `oracle_tests_pass_reference`
- `dummy_does_not_pass_all`
- `offline_reproducible_eval`

## Verification

Passed:

- `python3 -m py_compile tools/programbench_build_gym_instances.py tools/programbench_run_gym_dynamic_gates.py tools/programbench_select_gym_seed_repos.py`
- `python3 -m py_compile tools/programbench_build_github_candidate_instances.py`
- JSON validation for:
  - `configs/programbench_gym_mvp3.json`
  - `configs/programbench_gym_devset10.json`
  - `configs/programbench_gym_scale_seed_repos.json`
  - `configs/programbench_gym_external_pilot5.json`
- MVP-3 static rebuild to `/private/tmp/programbench_gym_mvp3_symlink_check_20260707_codex`: 3/3 static gates pass
- External pilot5 candidate skeleton build: 5/5 candidate static gates pass

## Current Limitation

The local run proves the static Gym factory path:

- metadata loads
- raw HF test blobs are found
- oracle-guarded bundles are linked
- source-leak/static path scans pass
- reward scripts are emitted
- binary-to-test sample schema is emitted

The following paper-grade gates still require a Linux x86-64 Docker host:

- materialize `cleanroom/executable`
- smoke-test reference binary
- run oracle tests on reference
- run oracle tests on dummy implementation
- verify no-network reproducibility
- add coverage/mutation instrumentation
