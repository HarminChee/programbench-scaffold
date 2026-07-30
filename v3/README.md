# ProgramBench Oracle Gym V3

V3 is an isolated, generalizable successor to V2. It does not modify V1/V2
scripts, runs, or generated suites.

## Start here

- [`RESULTS.md`](RESULTS.md) is the readable ten-repository result table.
- [`reports/go10_v3_release_results.json`](reports/go10_v3_release_results.json)
  is the machine-readable result, including duplicate metrics, timings, and
  quality status.
- [`published_tests/`](published_tests/) contains the final ten-repository
  publication. Chroma is represented by its independently quality-passed
  `r4`, `r5`, and `r6` shards.
- [`PUBLISHED_ARTIFACTS.json`](PUBLISHED_ARTIFACTS.json) records every archive's
  case count, byte size, source artifact, and SHA-256.
- [`REPRODUCE.md`](REPRODUCE.md) gives the end-to-end reproduction commands.

The core policy is:

```text
pinned repository + docs + native tests
  -> instance specification
  -> evidence-backed capability graph
  -> scenario and fixture plan
  -> multi-perspective agent generation
  -> retain all valid cases, including repetitions
  -> reference/gold behavior capture
  -> deterministic pytest oracle bundle
  -> assertion, dummy, repeat, source-leak, and binary-consistency gates
  -> empty/return-code-only rejection and strict-exact collapse
  -> source coverage plus optional dynamic-path attribution
  -> refinement from internal gaps
  -> absolute, PB-independent stopping gate
```

Target ProgramBench official oracle tests are never generation input. PB
coverage and PB test counts are held-out research baselines only.

## Layout

- `configs/`: cohort and instance specifications.
- `tools/`: V3-only planners, generators, controllers, metric adapters, and
  reporters.
- `published_tests/`: GitHub-friendly complete suites and validation evidence.
- `reports/`: the portable ten-repository release result and timing evidence.
- `runs/`: local immutable plans, prompts, model outputs, candidates, oracle
  bundles, coverage, and traces; intentionally ignored by Git because these
  artifacts are large and machine-specific.
- `smoke/`: disposable local validation runs; also ignored.

Each `published_tests/<repo>/` directory contains:

```text
README.md
CASE_INDEX.csv                   # GitHub-browsable case index
oracle_tests.tar.gz              # complete suite and fixtures
evidence/
  *.go_coverage_summary.json
  evaluation_quality_report.json
  pipeline_summary.json
```

The archive contains the full `generated_cli_manifest.json`, pytest adapter,
and all fixtures.

For Chroma, the same structure appears under `r4/`, `r5/`, and `r6/`. Run the
three shards independently; the published source-coverage result is their
block-wise `mode: set` profile union.

## Metrics

Primary acceptance for Go remains:

- Go statement coverage;
- executable-line coverage;
- per-file coverage;
- all quality and binary-consistency gates.

Optional secondary dynamic signals:

- Callgrind executed instruction/function/jump observations;
- QEMU user-mode translation-block trace and derived edge signatures, with
  first-party attribution when an unstripped binary and Go symbols are
  available;
- AFL-like edge bitmaps derived from stable dynamic edges, or `afl-showmap`
  when a compatible instrumented/binary-only runner exists.

Dynamic metrics are diagnostic novelty signals, not percentages of all
possible paths and not replacements for source-mapped coverage.

Every coverage collector records wall-clock runtime in seconds. Go reports
`statement_coverage_signal_seconds` as the coverage-instrumented pytest run
plus `covdata`/`textfmt`/`cover` post-processing, separately from the total
harness runtime. The dynamic runner reports per-case command and collector
times plus per-repository QEMU and Callgrind totals and means. This keeps signal
collection cost separate from cloning, compilation, and three-binary quality
verification.

V3 quality filtering does not treat an empty expected stdout or stderr fixture
as an empty test: asserting that a stream is completely empty is observable
behavior. It rejects cases with neither useful stimulus nor observation,
return-code-only weak oracles and strict duplicates with the same complete
invocation and behavior. Different inputs that happen to produce the same
result are retained by default: behavior-only pruning is unsafe before
source/dynamic novelty has been measured. A non-zero behavior-group cap remains
available only as an explicitly requested storage-control policy.

Refinement runs include PB-oracle-free feedback derived from the prior suite's
reference-binary captures and source coverage. Generation must first repair
dominant shallow failure clusters and establish viable behavior anchors. The
prompt also documents the portable file, loopback HTTP, terminal, and repeated
corpus fixture schemas and states that the runtime workspace contains no source
tree unless the case materializes one.

Later PB-free refinement rounds may use
`build_batches_v3.py --perspectives` to select a small generic subset such as
`source_corpus_harvest` and `protocol_and_fixture_matrix`. The selection must
come from our own gold-capture and source-coverage gaps; PB official tests
remain forbidden from the generation context.

For Go, whole-process QEMU/Callgrind observations include runtime and library
noise. V3 therefore reports first-party blocks and edges separately whenever
symbol attribution is possible. A stripped cleanroom binary can still produce
whole-process observations, but not a trustworthy first-party score.
`afl-showmap` is only reported as a formal AFL metric when the target is
instrumented or a working binary-only backend is present; a QEMU-derived
bitmap is labeled as an auxiliary AFL-like signal instead.

## First cohort

The initial V3 pilot uses:

1. `sclevine__yj.8016400`
2. `tomnomnom__gron.88a6234`
3. `multiprocessio__dsq.c3ae0ba`

All ten Go instances remain defined for later expansion.

## Script map

The main path through V3 is:

1. `build_plan_v3.py` builds the language-neutral instance specification,
   repository inventory, capability graph, scenario matrix, and fixture plan.
2. `validate_plan_v3.py` checks schema integrity and the held-out PB boundary.
3. `build_batches_v3.py` creates multi-perspective generation batches and,
   during refinement, PB-free reachability/anchor-repair batches.
4. `build_context_v3.py` creates bounded source/docs/native-test context.
5. `invoke_agent_v3.ps1`, `run_generation_worker_v3.ps1`, and
   `start_generation_worker_v3.ps1` call Claude Sonnet through Agent Maestro
   and persist restartable batch outputs.
6. `materialize_candidates_v3.py`, `normalize_fixture_dsl_v3.py`, and
   `combine_candidates_v3.py` parse portable CLI, file, Git, loopback HTTP,
   terminal, and `argv0` fixture definitions.
7. `run_go_v3_final.py` captures reference behavior, materializes deterministic
   pytest oracles, runs source coverage, and executes the quality gates.
8. `fast_quality_gates_v3.py` and `filter_low_value_cases_v3.py` reject empty,
   return-code-only, non-deterministic, source-leaking, dummy-passing, and
   strict-exact duplicate cases.
9. `build_refinement_feedback_v3.py` turns self-measured reachability and source
   gaps into the next PB-free refinement batch.
10. `evaluate_stop_v3.py` applies the absolute, PB-independent stopping rule.
11. `merge_captured_manifests_v3.py` and
    `merge_go_cover_profiles_v3.py` safely union compatible validated shards.
12. `run_dynamic_path_metrics_v3.py` and
    `run_qemu_timing_cohort_v3.py` collect optional QEMU/Callgrind/AFL-like
    novelty diagnostics and their runtime; these do not replace Go coverage.
13. `report_go10_v3.py`, `backfill_runtime_metrics_v3.py`, and
    `build_release_report_v3.py` produce auditable cohort reports.

The shared implementation used by both releases lives in the repository-level
[`tools/`](../tools/) directory. See
[`tools/README_ORACLE_GYM_SHARED.md`](../tools/README_ORACLE_GYM_SHARED.md).
