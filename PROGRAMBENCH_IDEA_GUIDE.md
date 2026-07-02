# ProgramBench Idea Guide

Date: 2026-06-19

## 0. Current Direction Update

The current project framing has been updated after discussion:

```text
Program-Analysis Scaffolding for Black-Box Program Reconstruction Agents
```

The MVP is now an agent-design/scaffold project, not an RL or model-training project. SFT/RL remain possible future work, but the first research milestone is to test whether a program-analysis scaffold can help the same baseline agent explore reference program behavior more systematically and reconstruct programs more accurately.

For the latest scaffold-centered documents, use:

- `README.md`
- `docs/proposal.md`
- `docs/experiment_plan.md`
- `docs/baseline_agents.md`
- `docs/workspace_structure.md`

Some older sections below still discuss dense RL and historical paths from the previous workspace. Treat them as background, not the current MVP scope.

This document is a handoff guide for starting a new workspace/thread focused only on the ProgramBench research direction. It intentionally does not depend on the VeriOffense project direction.

## 1. One-Sentence Summary

We want to study whether program-analysis-guided agents, combined with dense behavioral rewards, can improve black-box program reconstruction on ProgramBench.

In simpler terms: the agent only sees a compiled executable and documentation, repeatedly tests the program, observes behavior, and then rebuilds a functionally equivalent implementation from scratch.

## 2. What Robin's Comment Means

Robin's clarification was that ProgramBench is not mainly a cyber offense or PoC-generation benchmark. It should be treated as a separate possible research project.

The task is black-box program reconstruction:

```text
Given:
  reference executable + documentation

Not given:
  original source code

Agent must:
  run the reference program
  observe input-output behavior
  infer program functionality
  implement a new candidate program from scratch

Evaluation:
  hidden behavioral tests compare candidate behavior with reference behavior
```

This is a reverse-engineering-style task, but mostly behavioral reverse engineering, not binary decompilation.

## 3. Why This Is Interesting

ProgramBench is hard because it requires long-horizon software understanding. The agent must do more than write a function. It must infer a whole program's behavior, including:

- command-line arguments;
- input formats;
- stdout and stderr formatting;
- exit codes;
- file side effects;
- error handling;
- edge cases;
- hidden behavior not fully described in documentation.

The main hypothesis is that current coding agents fail partly because they lack a systematic black-box program-analysis loop. They may read docs and start coding too early, without enough active probing of the reference executable.

## 4. Proposed Research Direction

Tentative title:

```text
Program-Analysis-Guided Reinforcement Learning for Black-Box Program Reconstruction
```

Core idea:

```text
Use program analysis to help agents explore the reference program,
then use dense behavioral rewards to train or guide agents before final hidden-test success.
```

The important part is to reduce sparse reward. Instead of only giving reward when the full ProgramBench task is solved, we give intermediate reward when the candidate program matches more reference behaviors.

## 5. Method Loop

The proposed agent loop is:

```text
read documentation
  -> extract commands, flags, input/output formats
  -> generate behavior probes
  -> run reference executable
  -> record reference behavior
  -> infer partial specification
  -> generate candidate implementation
  -> run candidate on the same probes
  -> compare reference vs candidate
  -> cluster mismatches
  -> localize candidate-side causes
  -> repair candidate
  -> repeat
```

The key program-analysis components are:

- documentation parsing;
- CLI/API option discovery;
- active input generation;
- fuzzing-style edge-case exploration;
- differential testing;
- mismatch clustering;
- candidate coverage;
- fault localization;
- counterexample-guided repair.

## 6. Reward Design

The reward should not be just final pass/fail. A useful dense reward can include:

```text
compile_success
run_success
new_reference_behavior_discovered
valid_probe_generated
candidate_reference_stdout_match
candidate_reference_stderr_match
candidate_reference_exit_code_match
candidate_reference_file_effect_match
behavior_match_improvement
regression_penalty
final_hidden_test_success
```

A simple reward sketch:

```text
R =
  compile_reward
  + run_reward
  + mean_behavior_match_on_public_probes
  + novelty_reward_for_new_reference_behaviors
  - regression_penalty
  + final_hidden_test_reward
```

The reason this matters: if an agent is 80 percent correct, sparse reward may still look like zero. Dense behavior reward can tell the agent which behavior cluster is still wrong.

## 7. Current Local Progress

We cloned ProgramBench locally:

```text
/Users/harmin/Desktop/programbench/external/ProgramBench
```

Important local facts from the ProgramBench README:

- ProgramBench asks agents to rebuild programs from compiled binaries and documentation.
- Inference should run without internet access.
- Official Docker images are built for `linux/amd64`.
- Full inference/evaluation should be done on a Linux x86-64 machine, not natively on macOS.
- The official baseline is based on mini-swe-agent.
- Evaluation expects each submission as `submission.tar.gz` under a per-task directory.

We also built a small scaffold to test the core behavioral-reward idea before running full ProgramBench.

## 8. Existing Scaffold Files

Behavior probe generator:

```text
/Users/harmin/Desktop/programbench/tools/programbench_generate_probe_cases.py
```

This generates generic black-box probe cases. It currently supports:

- generic CLI probes;
- a `wc` profile with stdin, file input, missing file, and flags such as `-l`, `-w`, `-c`, and `-m`.

Behavior comparator:

```text
/Users/harmin/Desktop/programbench/tools/programbench_behavior_probe.py
```

This runs a reference command and optional candidate command in temporary sandboxes, then records:

- return code;
- stdout;
- stderr;
- stdout/stderr hashes;
- file side effects;
- exact match;
- partial reward.

Result summarizer:

```text
/Users/harmin/Desktop/programbench/tools/programbench_behavior_summary.py
```

This summarizes:

- exact-match count;
- mean partial reward;
- per-check pass rates;
- representative mismatch examples.

Partial demo candidate:

```text
/Users/harmin/Desktop/programbench/examples/programbench/wc_partial.py
```

This is a small hand-written partial reimplementation of `/usr/bin/wc`, used only to demonstrate that dense reward can distinguish partial correctness.

## 9. Current Toy Experiment

We used `/usr/bin/wc` as a toy reference program.

Generated 21 black-box probe cases:

```text
/Users/harmin/Desktop/programbench/reports/programbench_wc_probe_cases.json
```

Compared three settings:

```text
wc vs wc
wc vs cat
wc vs partial wc implementation
```

Results:

```text
wc vs wc:
  exact matches = 21 / 21
  mean partial reward = 1.0

wc vs cat:
  exact matches = 0 / 21
  mean partial reward = 0.5595

wc vs wc_partial.py:
  exact matches = 17 / 21
  mean partial reward = 0.9524
```

Interpretation:

- sparse final score only tells us whether everything passed;
- dense behavior score distinguishes completely wrong, partially correct, and nearly correct candidates;
- mismatch summaries reveal what to repair next;
- in the `wc_partial.py` case, failures were mainly stderr formatting for unsupported flags.

This supports the core idea that dense behavioral reward could guide agent improvement.

## 10. Reproduction Commands

Generate `wc` probe cases:

```bash
python3 tools/programbench_generate_probe_cases.py \
  --profile wc \
  --out reports/programbench_wc_probe_cases.json
```

Reference self-check:

```bash
python3 tools/programbench_behavior_probe.py \
  --reference-command '["/usr/bin/wc"]' \
  --candidate-command '["/usr/bin/wc"]' \
  --cases-file reports/programbench_wc_probe_cases.json \
  --out reports/programbench_behavior_probe_wc_self_autocases.json
```

Bad candidate:

```bash
python3 tools/programbench_behavior_probe.py \
  --reference-command '["/usr/bin/wc"]' \
  --candidate-command '["/bin/cat"]' \
  --cases-file reports/programbench_wc_probe_cases.json \
  --out reports/programbench_behavior_probe_wc_vs_cat_autocases.json
```

Partial candidate:

```bash
python3 tools/programbench_behavior_probe.py \
  --reference-command '["/usr/bin/wc"]' \
  --candidate-command '["python3","examples/programbench/wc_partial.py"]' \
  --cases-file reports/programbench_wc_probe_cases.json \
  --out reports/programbench_behavior_probe_wc_vs_partial_autocases.json
```

Summarize:

```bash
python3 tools/programbench_behavior_summary.py \
  reports/programbench_behavior_probe_wc_vs_partial_autocases.json \
  --out reports/programbench_behavior_summary_wc_vs_partial.json
```

## 11. Proposed MVP

The first real MVP should not try all ProgramBench tasks. Start with 5-10 easy CLI tasks.

MVP steps:

1. Set up a Linux x86-64 machine.
2. Install ProgramBench and mini-swe-agent baseline.
3. Select 5-10 easy ProgramBench CLI tasks.
4. Run official mini-swe-agent baseline.
5. Build a program-analysis scaffold around those tasks.
6. Generate reference behavior probes from docs and CLI exploration.
7. Run reference and candidate on the same probes.
8. Feed mismatch summaries back to the agent.
9. Compare baseline vs scaffold-assisted agent.

The immediate question is:

```text
Does program-analysis-guided probing improve behavioral test pass rate on easy ProgramBench tasks?
```

If yes, then move to SFT/RL.

## 12. Experiment Plan

Stage 1: Baseline reproduction

- Pick a small set of official easy tasks.
- Run mini-swe-agent baseline.
- Record hidden-test pass rate, solve rate, cost, reference queries, and build failures.

Stage 2: Program-analysis scaffold

- Add automatic probe generation.
- Add behavior database.
- Add differential testing.
- Add mismatch clustering.
- Give the agent structured mismatch summaries.

Stage 3: Trajectory collection

- Collect successful and failed reconstruction trajectories.
- Save documentation reads, probe inputs, reference outputs, candidate code patches, mismatch summaries, and final scores.
- Use the trajectories for supervised fine-tuning.

Stage 4: Dense RL

- Use compile success, runtime success, behavior match, improvement over prior iteration, and regression penalty as reward.
- Keep hidden tests separate from reward optimization.
- Compare dense reward against sparse final reward.

Stage 5: Scaling

- Expand from 5-10 easy tasks to all easy tasks.
- Add selected medium tasks.
- Analyze where the method fails.

## 13. Key Baselines and Ablations

Baselines:

```text
official mini-swe-agent baseline
documentation-only agent
agent + static doc extraction
agent + black-box probes
agent + probes + differential testing
agent + program analysis + dense reward
```

Core ablations:

```text
no program analysis
program analysis only
sparse RL only
program analysis + dense RL
```

Metrics:

```text
hidden-test pass rate
full task solve rate
95 percent test pass rate
compile success rate
runtime success rate
reference query count
token cost
wall-clock cost
build failure rate
regression count
mismatch reduction across iterations
```

## 14. Expected Contributions

Possible paper contributions:

1. A program-analysis-guided framework for black-box program reconstruction.
2. A dense behavioral reward design for ProgramBench-style tasks.
3. A reusable probing, differential testing, and repair scaffold.
4. An empirical study on whether structured program analysis helps coding agents.
5. Agent trajectories that can support SFT and RL.

## 15. Main Risks

Environment risk:

- Official ProgramBench Docker images are Linux x86-64.
- macOS is not a good full experiment environment.
- Use a Linux x86-64 server for baseline and evaluation.

Overfitting risk:

- The agent may overfit generated probes.
- Keep generated public probes separate from hidden evaluation tests.
- Use held-out behavior probes for internal validation.

Sparse improvement risk:

- Full solve rate may remain low in early experiments.
- Report partial metrics such as behavioral pass rate, 95 percent pass rate, and mismatch reduction.

Cost risk:

- Repeated reference probing and agent repair loops can be expensive.
- Track reference queries, token cost, and wall-clock cost.

## 16. What To Do First In A New Workspace

Recommended first checklist:

```text
1. Clone ProgramBench.
2. Install uv and ProgramBench.
3. Confirm `programbench --help` works.
4. Set up Linux x86-64 Docker environment.
5. Run one official evaluation smoke.
6. Run mini-swe-agent ProgramBench baseline on one easy task.
7. Port the behavior_probe scripts from this workspace.
8. Pick one task and generate reference probes.
9. Compare baseline agent vs scaffold-assisted agent.
10. Save all trajectories and results.
```

Useful commands from ProgramBench local README:

```bash
uvx programbench --help
uv run programbench eval /path/to/agent-run
uv run programbench info /path/to/agent-run
uv run programbench blob sync <instance_id>
```

Baseline entry point:

```bash
uvx --from mini-swe-agent mini-extra programbench --help
```

## 17. Handoff Prompt For A New Chat

Use this in a new chat/workspace:

```text
We are starting a new independent research direction based on ProgramBench, not VeriOffense.

Goal:
Study program-analysis-guided RL for black-box program reconstruction. The agent sees a compiled executable and documentation, but not source code. It should probe the reference program, infer behavior, implement a candidate program, compare candidate vs reference, and iteratively repair.

Current idea:
Use program analysis plus dense behavioral reward to reduce sparse final reward. Program-analysis components include documentation parsing, active input generation, differential testing, mismatch clustering, candidate coverage, fault localization, and counterexample-guided repair.

Current local progress:
We built a toy scaffold around `/usr/bin/wc`.
Files:
- tools/programbench_generate_probe_cases.py
- tools/programbench_behavior_probe.py
- tools/programbench_behavior_summary.py
- examples/programbench/wc_partial.py
- reports/programbench_direction_probe.md
- reports/programbench_behavior_summary_wc_vs_partial.json

Toy result:
wc vs wc = 21/21 exact, reward 1.0.
wc vs cat = 0/21 exact, reward 0.5595.
wc vs partial wc = 17/21 exact, reward 0.9524.

Next work:
Set up Linux x86-64 ProgramBench environment, run mini-swe-agent baseline on 5-10 easy CLI tasks, then compare baseline vs program-analysis scaffold.
```

## 18. Source Links

- ProgramBench GitHub: https://github.com/facebookresearch/programbench
- ProgramBench paper: https://arxiv.org/abs/2605.03546
- ProgramBench website/leaderboard: https://programbench.com
- ProgramBench tests on Hugging Face: https://huggingface.co/datasets/programbench/ProgramBench-Tests
- mini-swe-agent ProgramBench baseline docs: https://mini-swe-agent.com/latest/usage/programbench/
