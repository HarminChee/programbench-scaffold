# ProgramBench Scaffolding Project

Working title:

```text
Program-Analysis Scaffolding for Black-Box Program Reconstruction Agents
```

This workspace is now focused on ProgramBench as an independent agent-design research direction. The current MVP does not train or fine-tune models. It studies whether a program-analysis scaffold can help an existing coding agent reconstruct black-box programs more effectively.

## Core Idea

ProgramBench gives an agent a reference executable and documentation, but not the original source code. The agent must probe the executable, infer its behavior, and implement a fresh codebase whose executable matches the reference behavior.

Our scaffold sits around the agent and improves the early exploration and repair loop:

```text
docs + executable
  -> behavior probes
  -> reference traces
  -> structured behavior summary
  -> candidate implementation
  -> differential testing
  -> mismatch summary
  -> candidate repair
```

## Current Scope

In scope:

- agent scaffold design;
- black-box probe generation;
- reference/candidate behavior tracing;
- differential testing;
- mismatch clustering and summaries;
- A/B evaluation against a baseline agent.

Out of scope for the MVP:

- SFT;
- RL;
- reward-model training;
- full ProgramBench-scale evaluation on all tasks.

SFT/RL remain possible future work if the scaffold improves performance.

## Workspace Layout

```text
.
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
│   └── programbench_*.json / .md
└── external/
    └── ProgramBench/
```

## Local Smoke Test

The local MacBook can run the toy scaffold with `/usr/bin/wc`:

```bash
python3 tools/programbench_generate_probe_cases.py \
  --profile wc \
  --out reports/programbench_wc_probe_cases_current.json

python3 tools/programbench_behavior_probe.py \
  --reference-command '["/usr/bin/wc"]' \
  --candidate-command '["python3","examples/programbench/wc_partial.py"]' \
  --cases-file reports/programbench_wc_probe_cases_current.json \
  --out reports/programbench_behavior_probe_wc_vs_partial_current.json

python3 tools/programbench_behavior_summary.py \
  reports/programbench_behavior_probe_wc_vs_partial_current.json \
  --out reports/programbench_behavior_summary_wc_vs_partial_current.json
```

## ProgramBench Evaluation Note

Official ProgramBench inference/evaluation containers target `linux/amd64`. The full benchmark should run on a Linux x86-64 machine, not natively on this MacBook. Local work here is for scaffold development, toy validation, task inspection, and documentation.

Key upstream links:

- [ProgramBench GitHub](https://github.com/facebookresearch/programbench)
- [ProgramBench paper](https://arxiv.org/abs/2605.03546)
- [mini-swe-agent ProgramBench docs](https://mini-swe-agent.com/latest/usage/programbench/)

