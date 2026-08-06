# V2 Ten-Repository Results

This is the final V2 expansive cohort. The target repository's ProgramBench
official oracle tests were held out during generation and used only as a
post-generation baseline.

Count units are intentionally explicit:

- Native count: static Go `Test*` functions in the pinned repository.
- V2 count: behavioral cases in `generated_cli_manifest.json`; each case is one
  parametrized pytest execution.
- PB count: static pytest test functions across active official PB branches.

Coverage is reported as `executable-line / Go statement`.

| Repository | Native tests | V2 cases | PB tests | Native coverage | V2 coverage | PB coverage |
|---|---:|---:|---:|---:|---:|---:|
| yj | 6 | 855 | 651 | 75.9% / 76.2% | 89.3% / 90.3% | 88.5% / 88.8% |
| gron | 23 | 640 | 224 | 70.9% / 70.2% | 90.7% / 91.1% | 92.9% / 93.3% |
| dsq | 0 | 727 | 507 | - / 0.0% | 89.0% / 91.4% | 88.3% / 91.4% |
| go-mod-outdated | 13 | 473 | 266 | 100.0% / 100.0% | 97.4% / 97.2% | 100.0% / 100.0% |
| jplot | 0 | 1,023 | 546 | - / 0.0% | 32.5% / 36.3% | 77.9% / 81.1% |
| dupl | 10 | 335 | 360 | 57.1% / 57.8% | 89.1% / 90.0% | 93.8% / 94.6% |
| bat | 12 | 399 | 946 | 51.8% / 51.0% | 36.9% / 37.2% | unreliable external-HTTP baseline |
| cheat | 106 | 1,023 | 289 | 52.3% / 48.5% | 68.1% / 69.8% | 79.9% / 82.4% |
| scc | 257 | 1,024 | 464 | 73.3% / 70.6% | 66.6% / 64.6% | 92.0% / 91.3% |
| chroma | 63 | 674 | 192 | - / 0.0% | 58.3% / 54.9% | 88.0% / 88.8% |
| **Total** | **490** | **7,173** | **4,445** | - | - | - |

## Quality gates

All ten published V2 suites passed:

- generated pytest execution;
- cleanroom/source-built/coverage-built binary consistency;
- assertion lint;
- repeat/determinism validation;
- source-leak scan;
- dummy implementation rejection, with zero dummy-passing tests.

`bat`, `cheat`, and `scc` have known issues in the held-out PB official
baseline. These notes do not weaken the quality status of the published V2
suites; they only constrain direct PB-coverage interpretation.

## Duplicate diagnostics

These metrics operate on V2 manifest cases:

- Exact: normalized invocation and captured behavior are both identical.
- Structural: the same fixture/test structure remains after normalizing
  literals and paths.
- Invocation: normalized args, environment, stdin, files, timeout, and fixture
  state are identical.
- Behavior: captured return code, stdout, stderr, timeout, and observed file
  changes are identical.

| Repository | Exact | Structural | Invocation | Behavior |
|---|---:|---:|---:|---:|
| yj | 25.0% | 82.3% | 25.0% | 53.9% |
| gron | 19.7% | 82.0% | 19.7% | 48.6% |
| dsq | 19.9% | 74.0% | 19.9% | 61.1% |
| go-mod-outdated | 22.6% | 85.8% | 22.6% | 74.0% |
| jplot | 30.1% | 84.1% | 30.1% | 93.3% |
| dupl | 20.9% | 63.9% | 20.9% | 62.4% |
| bat | 30.8% | 69.7% | 30.8% | 93.0% |
| cheat | 62.5% | 83.6% | 62.5% | 81.3% |
| scc | 39.6% | 65.9% | 39.6% | 77.1% |
| chroma | 10.4% | 67.1% | 10.4% | 88.9% |

The exact machine-readable values and evidence paths are in
[`reports/go10_v2_release_results.json`](reports/go10_v2_release_results.json).
