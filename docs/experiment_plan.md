# Experiment Plan

## Goal

Evaluate whether a program-analysis scaffold improves black-box program reconstruction agents on ProgramBench.

The MVP question is:

```text
Does scaffold-assisted behavior discovery improve ProgramBench easy-task performance compared with the same agent without the scaffold?
```

## Current Constraint

The current machine is a MacBook. Official ProgramBench Docker containers target `linux/amd64`, so full inference and evaluation should run on a Linux x86-64 machine. Local Mac work should focus on:

- scaffold development;
- toy smoke tests;
- task metadata inspection;
- report generation;
- dry-run prompt and artifact design.

## Stage 0: Local Scaffold Validation

Purpose: verify that the scaffold scripts work independently of ProgramBench infrastructure.

Toy reference:

```text
/usr/bin/wc
```

Commands:

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

Expected behavior:

- self-check reference vs reference should reach 100% exact match;
- bad candidate such as `/bin/cat` should score low;
- partial candidate should score high but expose mismatch clusters.

## Stage 1: Linux x86-64 Setup

Run on a Linux x86-64 host:

```bash
uvx programbench --help
uvx --from mini-swe-agent mini-extra programbench --help
```

ProgramBench official evaluation expects a run directory like:

```text
agent-run/
├── task_id_1/
│   └── submission.tar.gz
├── task_id_2/
│   └── submission.tar.gz
└── ...
```

Evaluation:

```bash
uv run programbench eval /path/to/agent-run
uv run programbench info /path/to/agent-run
```

## Stage 2: Task Selection

Choose 5-10 easy CLI tasks. Avoid the synthetic `testorg__calculator.abc1234` sample.

Selection criteria:

- easy difficulty;
- CLI interface rather than GUI/TUI-heavy interface;
- low external dependency burden;
- small input/output surface;
- likely feasible within a small model budget;
- diverse languages across C, Go, Rust, and C++ if possible.

Initial candidate pool from local metadata:

```text
cmatsuoka__figlet.202a0a8
mgdm__htmlq.6e31bc8
sclevine__yj.8016400
rs__jplot.2a54bcc
multiprocessio__dsq.c3ae0ba
rbakbashev__elfcat.52f8cc7
sirwart__ripsecrets.34c9e03
anordal__shellharden.6a6ffd4
clog-tool__clog-cli.7066cba
miserlou__loop.209927c
```

Final selection should be confirmed on Linux after inspecting each task's cleanroom docs and executable behavior.

## Stage 3: Baseline Reproduction

Run official mini-swe-agent on the selected tasks with fixed:

- model;
- step budget;
- wall-clock budget;
- token budget;
- no-internet setting;
- task set;
- random seed if supported.

Record:

- final ProgramBench eval JSON;
- `programbench info` summary;
- agent trajectory logs;
- cost and token usage;
- build failures;
- number of candidate files and lines of code.

## Stage 4: Scaffold-Assisted Agent

Use the same base agent/model but insert a scaffold phase before implementation:

1. Inspect docs and executable metadata.
2. Generate reference probes.
3. Run reference on probes.
4. Build a behavior summary.
5. Give the behavior summary to the agent before implementation.
6. After candidate generation, run differential probes.
7. Give clustered mismatch summaries to the agent for repair.

The scaffold should not reveal hidden tests, original source, or internet-derived knowledge.

## Stage 5: Ablations

Core conditions:

```text
A. baseline mini-swe-agent
B. baseline + doc/help extraction summary
C. baseline + generated reference probes
D. baseline + probes + differential testing
E. baseline + probes + differential testing + mismatch clustering
```

Recommended first comparison:

```text
A vs E
```

Run ablations only after the full scaffold shows a measurable signal.

## Metrics

Primary:

- hidden-test pass rate;
- full task solve rate;
- 95% tests passed rate.

Secondary:

- compile success rate;
- runtime success rate;
- build failure rate;
- public probe exact-match rate;
- public probe partial-match score;
- mismatch reduction across repair iterations;
- number of reference queries;
- token cost;
- wall-clock cost;
- number of candidate revisions;
- regression count on prior probes.

Qualitative:

- whether agent explored before coding;
- whether agent used mismatch evidence;
- whether candidate architecture changed after feedback;
- whether failures cluster around spec recovery or implementation bugs.

## Artifact Schema

Each task run should save:

```text
runs/<date>/<condition>/<task_id>/
├── probe_cases.json
├── reference_behavior.json
├── behavior_summary.md
├── candidate_behavior_round_*.json
├── mismatch_summary_round_*.json
├── agent_trajectory.jsonl
├── submission.tar.gz
└── programbench_eval.json
```

## Success Criteria

The MVP is successful if scaffold-assisted runs show at least one of:

- higher hidden-test pass rate on most selected tasks;
- more tasks reaching 95% tests passed;
- lower build failure rate;
- clearer mismatch reduction across iterations;
- stronger gains for weaker/cheaper models.

Full solve-rate improvement is desirable but not required for the first milestone.

## Reporting

Report each task with a compact table:

```text
task | model | condition | hidden pass % | compile | public probe exact % | cost | notes
```

Then include 2-3 qualitative case studies showing how scaffold traces changed the agent's implementation decisions.

