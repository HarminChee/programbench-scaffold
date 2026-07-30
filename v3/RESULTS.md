# V3 Ten-Repository Results

This is the final V3 publication. The target repository's ProgramBench official
oracle tests were held out during generation and used only as a post-generation
baseline.

Count units are intentionally explicit:

- Native count: static Go `Test*` functions in the pinned repository.
- V3 count: behavioral cases in `generated_cli_manifest.json`; each case is one
  parametrized pytest execution.
- PB count: static pytest test functions across active official PB branches.

Coverage is reported as `executable-line / Go statement`.

| Repository | Native tests | V3 cases | PB tests | Native coverage | V3 coverage | PB coverage | V3 statement-signal time |
|---|---:|---:|---:|---:|---:|---:|---:|
| yj | 6 | 723 | 651 | 75.9% / 76.2% | 86.6% / 87.4% | 88.5% / 88.8% | 10.98 s |
| gron | 23 | 650 | 224 | 70.9% / 70.2% | 88.5% / 89.1% | 92.9% / 93.3% | 11.11 s |
| dsq | 0 | 464 | 507 | - / 0.0% | 88.5% / 91.1% | 88.3% / 91.4% | 55.53 s |
| go-mod-outdated | 13 | 440 | 266 | 100.0% / 100.0% | 97.4% / 97.2% | 100.0% / 100.0% | 5.71 s |
| jplot | 0 | 802 | 546 | - / 0.0% | 72.6% / 77.9% | 77.9% / 81.1% | 54.31 s |
| dupl | 10 | 174 | 360 | 57.1% / 57.8% | 86.9% / 88.0% | 93.8% / 94.6% | 2.60 s |
| bat | 12 | 1,252 | 946 | 51.8% / 51.0% | 56.4% / 59.5% | unreliable external-HTTP baseline | 244.30 s |
| cheat | 106 | 382 | 289 | 52.3% / 48.5% | 75.9% / 78.6% | 79.9% / 82.4% | 21.29 s |
| scc | 257 | 1,428 | 464 | 73.3% / 70.6% | 82.3% / 82.9% | 92.0% / 91.3% | 25.02 s |
| chroma | 63 | 1,365 | 192 | - / 0.0% | 75.3% / 73.5% | 88.0% / 88.8% | 42.26 s |
| **Total** | **490** | **7,680** | **4,445** | - | - | - | **473.11 s** |

The Chroma count is the sum of independently quality-passed R4, R5, and R6
shards. Its source coverage is a block-wise union of compatible Go
`mode: set` profiles, not a sum or average of percentages.

## Quality gates

All ten published V3 suites passed:

- generated pytest execution;
- cleanroom/source-built/coverage-built binary consistency;
- assertion lint;
- repeat/determinism validation;
- source-leak scan;
- all dummy implementations rejected.

`bat`, `cheat`, and `scc` have known issues in the held-out PB official
baseline. These notes only constrain direct PB-baseline interpretation.

## Duplicate diagnostics

The definitions are identical to the V2 release. Chroma is measured across the
union of its three published manifests, so cross-shard duplicates are visible.

| Repository | Exact | Structural | Invocation | Behavior |
|---|---:|---:|---:|---:|
| yj | 0.0% | 79.9% | 0.0% | 52.7% |
| gron | 0.0% | 79.8% | 0.0% | 39.5% |
| dsq | 0.0% | 62.1% | 0.0% | 60.3% |
| go-mod-outdated | 0.0% | 89.3% | 0.0% | 45.5% |
| jplot | 0.0% | 78.3% | 0.0% | 80.3% |
| dupl | 0.0% | 65.5% | 0.0% | 26.4% |
| bat | 0.0% | 73.1% | 0.0% | 85.1% |
| cheat | 0.0% | 45.8% | 0.0% | 43.5% |
| scc | 0.0% | 55.7% | 0.0% | 50.3% |
| chroma | 3.4% | 75.3% | 3.4% | 68.3% |

The exact machine-readable values, timings, and publication paths are in
[`reports/go10_v3_release_results.json`](reports/go10_v3_release_results.json).
