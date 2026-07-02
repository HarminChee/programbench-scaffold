# ProgramBench Direction Probe

Date: 2026-06-18

## Why This Is Separate From VeriOffense

Robin's clarification means ProgramBench is not mainly about cyber offense or PoC generation.
It is a separate possible collaboration direction: train a coding agent to observe a black-box program's behavior and rebuild a functionally equivalent program from scratch.

The relevant research angle is program analysis plus RL:

- generate behavior probes for the reference binary;
- observe stdout, stderr, return code, file effects, and timing;
- use dense partial reward to guide the agent before it passes all hidden tests;
- prevent reward hacking by separating public probes from hidden verification.

## Scaffold Built

- Added `tools/programbench_generate_probe_cases.py`.
  - Generates generic CLI probe cases.
  - Includes a `wc` profile with stdin, file input, missing file, and flags such as `-l`, `-w`, `-c`, and `-m`.
- Added `tools/programbench_behavior_probe.py`.
  - Runs a reference command and an optional candidate command in temporary sandboxes.
  - Captures return code, stdout/stderr, output hashes, file side effects, and per-case comparison.
  - Computes exact match and partial reward.
- Added `tools/programbench_behavior_summary.py`.
  - Summarizes exact matches, check-level pass rates, and representative mismatches.
- Added `examples/programbench/wc_partial.py`.
  - A small hand-written partial reconstruction of `/usr/bin/wc`.
  - Used only as a demo candidate to show whether dense behavior reward can distinguish partial progress.

## Small Experiment

Generated 21 black-box probe cases:

- `reports/programbench_wc_probe_cases.json`

Compared three candidates against `/usr/bin/wc`:

- reference self-check: `/usr/bin/wc` vs `/usr/bin/wc`;
- bad candidate: `/usr/bin/wc` vs `/bin/cat`;
- partial candidate: `/usr/bin/wc` vs `examples/programbench/wc_partial.py`.

Results:

- self-check: 21 / 21 exact matches, mean partial reward = 1.0.
- `cat`: 0 / 21 exact matches, mean partial reward = 0.5595.
- `wc_partial.py`: 17 / 21 exact matches, mean partial reward = 0.9524.

Result files:

- `reports/programbench_behavior_probe_wc_self_autocases.json`
- `reports/programbench_behavior_probe_wc_vs_cat_autocases.json`
- `reports/programbench_behavior_probe_wc_vs_partial_autocases.json`
- `reports/programbench_behavior_summary_wc_self.json`
- `reports/programbench_behavior_summary_wc_vs_cat.json`
- `reports/programbench_behavior_summary_wc_vs_partial.json`

## Interpretation

This confirms the core experimental idea for the ProgramBench direction:

- sparse final score can only say whether all behavior tests pass;
- dense behavior reward can tell whether a candidate is completely wrong, partially correct, or nearly correct;
- check-level reward can point to specific gaps, such as stdout mismatch, stderr formatting mismatch, return-code mismatch, or file-effect mismatch.

The partial `wc` candidate failed only 4 cases, all due to stderr formatting for unsupported flags.
That is a useful RL signal: the agent does not need to rediscover the whole program at once; it can improve a specific behavior cluster.

## Next ProgramBench Experiments

1. Move from toy `/usr/bin/wc` to one small official ProgramBench task.
   - Start with an easy CLI task because official Docker images are `linux/amd64` and may be slow on Apple Silicon.
   - First inspect docs, binary interface, and official tests before attempting reconstruction.

2. Add coverage-guided probe generation.
   - Current probes are static.
   - Next version should use mismatch clusters to generate more focused behavior probes, similar to ProgramBench's iterative test-generation loop.

3. Add a simple RL/edit loop.
   - Candidate code is edited by an agent.
   - Reward is exact matches plus check-level partial reward.
   - Hidden cases are held out to detect overfitting.

4. Add program-analysis features.
   - CLI option discovery from help/error messages.
   - Input-output clustering.
   - File side-effect inference.
   - Differential probes for edge cases.

This direction is related to VeriOffense only at the benchmark-construction level.
Both need public spec vs hidden verification, partial reward, and anti-reward-hacking design.
The task goal is different: ProgramBench rebuilds program functionality, while VeriOffense verifies vulnerability-triggering behavior.
