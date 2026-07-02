# Proposal: Program-Analysis Scaffolding for Black-Box Program Reconstruction Agents

## One-Sentence Pitch

We propose an agent scaffold that systematically explores a black-box reference executable, summarizes its observable behavior, and feeds structured behavioral evidence to coding agents so they can reconstruct functionally equivalent programs more reliably on ProgramBench.

## Motivation

ProgramBench asks whether language-model software agents can rebuild complete programs from scratch. The agent receives a compiled executable and usage documentation, but not the original source. It must produce a fresh source codebase and build script whose executable matches the reference program's behavior.

This is hard because the model must recover the program specification before it can implement the program. The missing specification includes command-line options, input formats, stdout/stderr conventions, exit codes, file side effects, edge cases, and error handling. Current coding agents often start writing code too early, with limited evidence about how the reference executable behaves.

The ProgramBench paper frames the worker as an LM equipped with an agent scaffold, and reports that existing models make meaningful partial progress but struggle with full reconstruction. That makes scaffold design itself a natural research target: can a better behavior-discovery scaffold improve ProgramBench performance without changing the underlying model?

## Research Thesis

The main hypothesis is:

```text
A program-analysis scaffold can improve black-box program reconstruction by giving agents broader, more systematic behavioral evidence before and during implementation.
```

This project is currently about agent design, not model training. We do not need SFT or RL to produce the first publishable signal. The MVP compares the same base agent with and without the scaffold.

## Task Setting

Given:

- reference executable;
- documentation and usage files;
- ability to run the reference program in the task environment.

Not given:

- original source code;
- hidden tests;
- internet access during inference.

Agent output:

- a fresh source codebase;
- a build script that produces a candidate executable.

Evaluation:

- hidden behavioral tests compare candidate behavior against reference behavior;
- observable behavior includes stdout, stderr, exit status, file effects, and related external outputs.

## Proposed Scaffold

The scaffold is a modular wrapper around a coding agent. Its job is to make the black-box exploration phase explicit, reproducible, and information-rich.

### 1. Documentation Analyzer

Extracts likely commands, flags, subcommands, positional arguments, input formats, examples, and output descriptions from docs and help text.

### 2. Probe Generator

Generates behavior probes before coding begins. Initial probe families include:

- `--help`, `-h`, `--version`, invalid flags;
- empty stdin and seed stdin;
- small text, CSV, JSON, and malformed inputs;
- missing files and empty files;
- combinations of documented flags;
- task-specific probes derived from examples.

### 3. Behavior Recorder

Runs the reference executable in isolated workdirs and records:

- command;
- stdin;
- input files;
- return code;
- stdout and stderr;
- hashes;
- files created/modified after execution;
- runtime and timeout status.

### 4. Behavior Summary Builder

Compresses raw traces into an agent-readable behavior spec. The goal is not to dump logs, but to expose useful structure:

- valid invocation patterns;
- error message templates;
- output formatting rules;
- edge cases;
- inferred invariants;
- unexplored regions.

### 5. Differential Tester

After the agent writes a candidate, the scaffold runs reference and candidate on the same probes and compares observable behavior.

### 6. Mismatch Clusterer

Groups failures by behavioral category:

- stdout mismatch;
- stderr mismatch;
- exit-code mismatch;
- file-effect mismatch;
- timeout/crash;
- unsupported option;
- format drift.

### 7. Repair Prompt Adapter

Feeds concise counterexamples back to the agent:

```text
For probe X, reference returned code 1 and stderr "...".
Candidate returned code 0 and stderr "".
Likely issue: invalid-flag handling.
Patch only the relevant argument parser/error path.
```

## Research Questions

1. Does a behavior-discovery scaffold improve hidden-test pass rate over a baseline agent?
2. Does it improve partial metrics such as behavioral pass rate, compile success, and mismatch reduction?
3. Which scaffold stage contributes most: doc extraction, probe generation, differential testing, or mismatch summaries?
4. How should behavioral evidence be represented for agents: raw traces, structured tables, inferred specs, or clustered counterexamples?
5. Does the scaffold help weaker/cheaper models disproportionately more than frontier models?

## MVP

The first MVP should run on 5-10 easy ProgramBench CLI tasks.

Compare:

```text
Baseline agent
vs.
Baseline agent + program-analysis scaffold
```

The scaffold-assisted condition should use the same model, task budget, and inference constraints as the baseline, with the only intended difference being the structured behavior-discovery component.

## Expected Contributions

1. A program-analysis scaffold for black-box program reconstruction agents.
2. A reproducible behavior-probing and differential-testing workflow.
3. An empirical comparison of baseline agents versus scaffold-assisted agents on ProgramBench easy tasks.
4. An ablation study of scaffold components.
5. A trajectory dataset of probes, behavior summaries, candidate revisions, and mismatch clusters for future SFT/RL.

## Non-Goals For The First Stage

- Training a new model.
- Optimizing dense RL rewards.
- Solving all 200 ProgramBench tasks.
- Using internet access or original source code during inference.
- Wrapping the reference executable as the solution.

## Risks

The main infrastructure risk is that official ProgramBench containers target Linux x86-64. The MacBook workspace is suitable for local scaffold development and toy tests, but not for full official evaluation.

The main methodological risk is overfitting to generated probes. We address this by treating scaffold probes as public exploration data and evaluating only on hidden ProgramBench tests.

The main research risk is that the scaffold may improve public behavior match but not hidden tests. For that reason, the experiment tracks both hidden scores and intermediate mismatch-reduction metrics.

## Future Work

If the scaffold improves agent behavior, the resulting trajectories can support:

- supervised fine-tuning on good black-box exploration traces;
- dense behavioral reward design;
- RL over repair loops;
- task-adaptive probe generation;
- multi-agent exploration and implementation splits.

These are future extensions, not the MVP.

## Sources

- [ProgramBench GitHub](https://github.com/facebookresearch/programbench)
- [ProgramBench paper](https://arxiv.org/abs/2605.03546)
- [mini-swe-agent ProgramBench documentation](https://mini-swe-agent.com/latest/usage/programbench/)

