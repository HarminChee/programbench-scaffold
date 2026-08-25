# PB-external repository selection

A production cohort should be useful, reproducible and genuinely outside the
ProgramBench corpus. Selection is an evidence task, not a popularity contest.

## Required gates

- Not present in the pinned ProgramBench repository/commit catalog, including
  aliases, forks and renamed projects.
- A real CLI with a stable, local, observable interface. Reject libraries whose
  binary is only an example and programs whose core behavior requires a live
  service, account, credential, GUI or public network.
- An immutable full commit and license suitable for research redistribution.
- Source builds in the selected container; all dependencies can be prefetched
  and then rebuilt with `network=none`.
- Deterministic tiny fixtures can exercise the main behavior families. Time,
  locale, HOME, terminal geometry, randomness and concurrency can be bounded.
- The executable is reachable by the oracle harness and supports meaningful
  stdout/stderr/exit-code or filesystem-state assertions.
- Native tests and documentation provide behavior seeds, but PB official tests
  for the target are absent and never exposed to generation.
- First-party coverage attribution can exclude dependencies, generated files,
  vendored code and language runtime.

## Difficulty and cohort balance

Prefer small and medium CLIs for the main cohort: parsers, formatters,
transformers, filesystem tools and bounded stateful commands. Include a small
minority of broader multi-subcommand or multi-format tools to test scaling.
Avoid filling a cohort with near-identical text filters. Record source bytes,
source files, native-test footprint, subcommands, input modes, state/network/
concurrency signals and the reason for the assigned difficulty.

Retained-case targets are ranges after quality filtering. For ordinary Go and
Rust CLIs, 500–1,500 cases is a common PB-like scale; larger or behaviorally
broader programs may justify 1,000–3,000. Counts never override behavior-family
completeness, determinism, dummy rejection, source-leak checks or saturation.

## Preflight record

For every admitted repository persist: upstream URL, commit, license, language,
binary target, source-tree hash, source-size metrics, build/native-test commands,
dependency manifests, immutable image ID, offline-build result, native active
test count, native first-party coverage status, behavior themes, exclusions and
all rejection/repair notes. A failed gate is explicit; it is not converted to
zero coverage.
