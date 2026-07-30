# ProgramBench Oracle Gym V2

V2 is an isolated successor to the original Go oracle workflow. It does not
modify V1 scripts, V1 runs, or the ten existing generated suites.

## Start here

- [`RESULTS.md`](RESULTS.md) is the readable ten-repository result table.
- [`reports/go10_v2_release_results.json`](reports/go10_v2_release_results.json)
  is the machine-readable result, including duplicate metrics and quality
  gates.
- [`published_tests/`](published_tests/) contains the ten generated suites.
- [`PUBLISHED_ARTIFACTS.json`](PUBLISHED_ARTIFACTS.json) records every archive's
  case count, byte size, source artifact, and SHA-256.
- [`REPRODUCE.md`](REPRODUCE.md) gives the end-to-end reproduction commands.

V2 changes the control objective from *coverage-first generation* to
*behavior-matrix-guided generation*:

```text
source + docs + native tests
  -> behavior map
  -> scenario matrix
  -> deterministic fixture catalog
  -> topic-batch generation
  -> reference capture and strong oracle construction
  -> quality, dummy, three-binary, and semantic-novelty gates
  -> matrix-gap refinement
  -> coverage measurement and reporting
```

The target repository's ProgramBench official Oracle tests remain forbidden
generation input. They may only be used after generation for held-out
evaluation.

## Layout

* `tools/` - V2-only planners, validators, and controllers.
* `configs/` - immutable cohort definitions and run settings.
* `published_tests/` - GitHub-friendly complete suites and evidence.
* `reports/` - the portable cohort result.
* `runs/` - local per-instance plans, agent batches, and final artifacts;
  intentionally ignored by Git because they are large and machine-specific.
* `smoke/` - disposable local validation runs; also ignored.

Each `published_tests/<repo>/` directory contains:

```text
README.md
CASE_INDEX.csv                   # GitHub-browsable case index
oracle_tests.tar.gz              # complete suite and fixtures
evidence/
  *.go_coverage_summary.json
  evaluation_quality_report.json
  pipeline_summary.json          # where recorded
```

The archive contains the full `generated_cli_manifest.json`, pytest adapter,
and all fixtures.

## First commands in WSL

```bash
python3 /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v2/tools/build_behavior_plan_v2.py \
  sclevine__yj.8016400 \
  --source-dir /path/to/pinned/source \
  --output-dir /path/to/v2/run/plan

python3 /mnt/c/Users/v-haominqi/Documents/Codex/pb-scaffold/v2/tools/validate_behavior_plan_v2.py \
  --plan-dir /path/to/v2/run/plan
```

V2 outputs are deliberately separate from the historical V1 experiment roots.

## Quality-loop repair

`tools/repair_candidates_from_quality_v2.py` creates a new candidate manifest
when the dummy gate finds weak cases. It removes only cases identified by the
stable generated-test index and records each removal in `repair_history`; it
never mutates a previous manifest or any V1 artifact.

For a cohort run, `build_topic_batches_v2.py --grouped` uses three bounded
topics (`interface_and_errors`, `semantic_core`, and `state_and_fixtures`).
This preserves behavior-family coverage while avoiding one model call per
small family.

`start_v2_generation_worker.ps1` starts a recoverable background worker for
one instance. It writes `generation_worker_status.json` plus timestamped logs,
and skips batch manifests that already exist on restart.

`merge_candidate_batches_v2.py` combines topic outputs and removes only cases
whose executable fixture DSL is exactly identical. It records every removal;
behavior-level novelty is evaluated later, after reference capture.

`augment_yj_semantic_matrix_v2.py` is the first repo-specific fixture planner.
It adds yj's source-derived format/flag/corpus matrix to agent proposals and
removes an accidentally included executable name from agent argv. It does not
read PB official Oracle tests.

Expansive mode uses `build_topic_batches_v2.py --expansive`. Each topic is
sampled from four different perspectives and prompts explicitly request large
enumerations. `combine_candidate_batches_expansive_v2.py` retains repeated
invocations and templates instead of optimizing for a minimal suite.

## Script map

The main path through V2 is:

1. `build_behavior_plan_v2.py` inventories source, documentation, native tests,
   entry points, state surfaces, and behavior families.
2. `validate_behavior_plan_v2.py` rejects malformed or incomplete plans.
3. `build_topic_batches_v2.py` turns the plan into focused generation batches;
   `--expansive` requests multiple high-volume perspectives.
4. `build_agent_context_v2.py` creates bounded source/docs/native-test context.
   Target PB oracle tests are never included.
5. `invoke_v2_agent_batch.ps1`, `run_v2_generation_worker.ps1`, and
   `start_v2_generation_worker.ps1` call Claude Sonnet through Agent Maestro
   and persist recoverable batch outputs.
6. `materialize_agent_cases_v2.py`,
   `merge_candidate_batches_v2.py`, and
   `combine_candidate_batches_expansive_v2.py` parse and combine candidate
   fixture DSL.
7. `sanitize_candidates_v2.py` and
   `filter_external_network_cases_v2.py` make the candidates executable and
   deterministic without reading PB official tests.
8. `run_go_v2_final_manual.py` captures gold behavior, builds pytest oracles,
   applies quality gates, and runs the Go coverage harness.
9. `run_quality_gates_v2.py`,
   `filter_candidates_from_capture_quality_v2.py`, and
   `repair_candidates_from_quality_v2.py` remove or repair weak, unstable, or
   dummy-passing cases.
10. `report_go10_v2_results.py` produces the final coverage, quality, and
    duplicate report.

The shared implementation used by both releases lives in the repository-level
[`tools/`](../tools/) directory. See
[`tools/README_ORACLE_GYM_SHARED.md`](../tools/README_ORACLE_GYM_SHARED.md).
