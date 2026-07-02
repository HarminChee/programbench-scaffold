# Workspace Structure

This workspace is organized as a clean ProgramBench scaffolding project.

```text
/Users/harmin/Desktop/programbench
├── README.md
├── PROGRAMBENCH_IDEA_GUIDE.md
├── docs/
│   ├── proposal.md
│   ├── experiment_plan.md
│   ├── baseline_agents.md
│   └── workspace_structure.md
├── tools/
│   ├── programbench_generate_probe_cases.py
│   ├── programbench_behavior_probe.py
│   └── programbench_behavior_summary.py
├── examples/
│   └── programbench/
│       └── wc_partial.py
├── reports/
│   ├── programbench_direction_probe.md
│   ├── programbench_wc_probe_cases.json
│   ├── programbench_behavior_probe_*.json
│   └── programbench_behavior_summary_*.json
└── external/
    └── ProgramBench/
```

## Directories

### `docs/`

Research planning documents:

- `proposal.md`: current scaffold-centered research proposal;
- `experiment_plan.md`: MVP experiment design;
- `baseline_agents.md`: baseline harness/model choices;
- `workspace_structure.md`: this file.

### `tools/`

Standalone scaffold utilities:

- `programbench_generate_probe_cases.py`: generates black-box CLI probe cases;
- `programbench_behavior_probe.py`: runs reference/candidate commands and records observable behavior;
- `programbench_behavior_summary.py`: summarizes exact matches, partial check rates, and mismatch examples.

### `examples/programbench/`

Toy candidates for local smoke tests.

Current file:

- `wc_partial.py`: partial reimplementation of `/usr/bin/wc`.

### `reports/`

Historical and current scaffold outputs.

Old report files were migrated from the previous workspace. Some historical reports may still contain old absolute paths from `/Users/harmin/Desktop/VeriOffense`; newly generated reports should use the current workspace paths.

### `external/ProgramBench/`

Local copy of the upstream ProgramBench repository used for docs, task metadata, and eventual official evaluation.

## Current Scaffold Flow

```text
generate probes
  -> run reference and candidate
  -> compare observable behavior
  -> summarize mismatch clusters
```

The current scripts are intentionally simple and dependency-free. The next engineering step is to add a task-level runner that can:

- read a ProgramBench task cleanroom directory;
- run help/documentation probes against its executable;
- create `behavior_summary.md`;
- inject that summary into an agent prompt;
- capture post-candidate differential results.

## Mac vs Linux

Local Mac:

- OK for toy scaffold;
- OK for metadata inspection;
- OK for document and prompt design.

Linux x86-64:

- required for official ProgramBench Docker inference/evaluation;
- required for fair mini-swe-agent baseline reproduction.

