# ProgramBench Gym

Last updated: 2026-07-07

## Purpose

ProgramBench Gym is the instance factory and reward environment for the
binary-to-test direction.

It has two jobs:

1. Convert repositories into ProgramBench-style instances:
   `README/docs + reference binary + hidden executable tests`.
2. Score generated executable tests so they can become binary-to-test training
   data or RL feedback.

The Gym is not a replacement for ProgramBench. It is the production path for
making more ProgramBench-like instances and for evaluating test-generating
agents before downstream coding-agent evaluation.

## Instance Contract

Each instance uses this directory shape:

```text
gym_instances/<instance_id>/
├── metadata.json
├── cleanroom/
│   ├── README.md
│   └── executable                     # optional until Docker materialization succeeds
├── private/
│   ├── source_manifest.json
│   ├── raw_test_blobs.json
│   ├── coverage/
│   └── mutants/
├── oracle_tests/
│   └── sanitized/                     # source-leak-guarded executable tests
├── eval/
│   ├── README.md
│   ├── run_reference_smoke.py
│   ├── score_generated_tests.py
│   └── reward_schema.json
├── binary_to_test_sample.json
└── quality_report.json
```

The `private/` subtree can contain source, raw test blob paths, coverage, and
mutation artifacts. It must never be exposed to an inference agent.

The `cleanroom/`, `oracle_tests/sanitized/`, and `eval/` subtrees define the
training/evaluation surface. For ProgramBench-derived bootstrap instances, the
cleanroom executable is materialized from the official `task_cleanroom_v6`
Docker image when available.

## Metadata Fields

`metadata.json` is the durable schema for consumers:

- `schema_version`: currently `0.1`.
- `instance_id`: ProgramBench-style id.
- `source_type`: `programbench_existing` or `github_repo`.
- `repository`, `commit`, `language`, `difficulty`.
- `image_name`, `image_tags`: ProgramBench-compatible Docker image refs.
- `cleanroom`: docs and reference executable materialization status.
- `oracle_tests`: raw branch blob summary plus sanitized bundle path.
- `evaluation`: scripts and reward schema.
- `quality_gates`: static and dynamic gate results.

## Quality Gates

The Gym builder records these gates in `quality_report.json`:

| gate | meaning |
| --- | --- |
| `metadata_loaded` | task/repo metadata is present and parseable |
| `raw_test_blobs_found` | branch test archives are present locally |
| `sanitized_bundle_found` | source-leak-guarded oracle bundle exists |
| `no_source_leakage_static` | sanitized bundle has no obvious source/build leakage |
| `no_source_path_leakage` | agent-visible manifest does not reveal host/source blob paths |
| `reference_binary_materialized` | cleanroom executable was copied from Docker image |
| `reference_smoke_runs` | reference executable can run a small smoke probe |
| `oracle_tests_pass_reference` | oracle tests pass on the reference binary |
| `dummy_does_not_pass_all` | dummy implementation does not satisfy the tests |
| `offline_reproducible_eval` | eval path works with no internet dependency |
| `coverage_or_mutation_ready` | hooks exist for coverage/mutation rewards |

For the first Mac-local v0, the dynamic Docker gates may be `skipped`. The
static gates must pass before using an instance for training data.

## Bootstrap Plan

1. MVP-3 bootstrap from ProgramBench official tasks:
   `sclevine__yj.8016400`, `sirwart__ripsecrets.34c9e03`,
   `cmatsuoka__figlet.202a0a8`.
2. Expand to the 10-task dev set:
   `ripsecrets`, `csview`, `code-minimap`, `clog-cli`, `datasurgeon`, `yj`,
   `dsq`, `jplot`, `figlet`, `jp2a`.
3. Use the same schema for new GitHub repos after the ProgramBench-compatible
   builder/verifier is stable.

## Current Commands

Build the MVP-3 ProgramBench-derived Gym instances:

```bash
python3 tools/programbench_build_gym_instances.py \
  --config configs/programbench_gym_mvp3.json \
  --output-root reports/programbench_gym_mvp3_current
```

Build the full 10-task dev set:

```bash
python3 tools/programbench_build_gym_instances.py \
  --config configs/programbench_gym_devset10.json \
  --output-root reports/programbench_gym_devset10_current
```

Run Linux/Docker dynamic gates through GitHub Actions:

```text
Actions -> ProgramBench Gym Dynamic Gates
config: configs/programbench_gym_mvp3.json
output_root: reports/programbench_gym_dynamic_mvp3
sync_blobs: true
```

The workflow calls:

```bash
python3 tools/programbench_run_gym_dynamic_gates.py \
  --config configs/programbench_gym_mvp3.json \
  --output-root reports/programbench_gym_dynamic_mvp3 \
  --runner docker \
  --prepare-bundles \
  --sync-blobs \
  --overwrite
```

Select the first wave of new GitHub repo candidates:

```bash
python3 tools/programbench_select_gym_seed_repos.py \
  --out reports/programbench_gym_scale_seed_repos_current.json \
  --md-out reports/programbench_gym_scale_seed_repos_current.md \
  --target-total 20 \
  --per-query 50 \
  --resolve-commits
```

Build the first external candidate-only pilot skeletons:

```bash
python3 tools/programbench_build_github_candidate_instances.py \
  --config configs/programbench_gym_external_pilot5.json \
  --output-root reports/programbench_gym_external_pilot5_candidates \
  --overwrite
```

On a Linux x86-64 host with ProgramBench Docker images, rerun the instance
builder with `--materialize-cleanroom` to copy `/workspace` from
`<image>:task_cleanroom_v6` and activate dynamic gates.

## Reward Interface

Generated tests are scored by `eval/score_generated_tests.py` with this first
reward surface:

- tests pass on reference executable;
- tests fail on a dummy executable;
- tests are deterministic across repeated runs;
- tests finish within timeout;
- optional future hooks for coverage, mutation score, and downstream
  coding-agent score.

The generated-test convention is intentionally simple: tests should execute
`./executable` in their working directory. The scorer creates that executable as
a symlink to the reference binary or as a dummy program during reward checks.

## Current Build Outputs

As of 2026-07-07:

- MVP-3 was regenerated in `/private/tmp` after cleanup; 3/3 static gates pass.
- The 10-task dev-set config remains ready for regeneration; prior generated
  `reports/programbench_gym_*` outputs were removed as disposable artifacts.
- `reports/test_bundles_oracle_guarded_2026-07-07` is intentionally retained as
  the local sanitized oracle-bundle cache used by the Gym builder.
- `.github/workflows/programbench-gym-dynamic-gates.yml` is the current Ubuntu
  Linux x86-64 path for materialization and dynamic gate validation.
- `reports/programbench_gym_external_pilot5_candidates` was generated as a
  lightweight candidate-only skeleton: 5/5 candidate static gates pass, but 0/5
  are accepted training instances until clone/build/test/dummy/offline gates run.

Dynamic gates are intentionally still skipped on the local Mac run:
`reference_binary_materialized`, `reference_smoke_runs`,
`oracle_tests_pass_reference`, `dummy_does_not_pass_all`, and
`offline_reproducible_eval` require a Linux x86-64 Docker host for paper-grade
validation.
