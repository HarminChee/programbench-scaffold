# ProgramBench Oracle Gym: Reproducible V2 and V3 Releases

This branch publishes two independent, reproducible snapshots of the Go
oracle-test construction workflow. Start with the version-specific README:

- [V2 release](v2/README.md) - behavior-map and scenario-matrix generation,
  expansive multi-perspective sampling, gold capture, and strict quality gates.
- [V3 release](v3/README.md) - generalized capability planning, portable
  fixture DSL, low-value filtering, reachability repair, absolute stopping
  criteria, and optional dynamic-path diagnostics.

Each version contains:

```text
vN/
├── README.md                 # Entry point and file map
├── REPRODUCE.md              # End-to-end commands and prerequisites
├── RESULTS.md                # Human-readable ten-repository results
├── PUBLISHED_ARTIFACTS.json  # Counts, paths, archive sizes, and SHA-256
├── configs/                  # Cohort and coverage configuration
├── tools/                    # Version-specific workflow implementation
├── reports/                  # Machine-readable release result
└── published_tests/          # Complete generated oracle suites
```

The complete suites are committed as deterministic `oracle_tests.tar.gz`
archives to avoid tens of thousands of tiny fixture files in Git. Every suite
also exposes a compact case index and validation evidence as normal browsable
files; the full manifest and pytest adapter remain inside the archive.

The target ProgramBench official oracle tests were held out from generation.
They are used only as post-generation baselines in the result reports.
