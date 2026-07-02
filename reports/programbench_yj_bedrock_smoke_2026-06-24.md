# ProgramBench Bedrock Smoke Run: yj

Date: 2026-06-24

## Goal

Run one small ProgramBench instance on the MacBook using AWS Bedrock as the model backend, then compare the original mini-swe-agent baseline with an oracle-spec-assisted variant.

This is a smoke run, not a final experiment. The MacBook runs Linux x86-64 ProgramBench containers through Docker/Rosetta emulation, so evaluation is much slower than on a real Linux x86-64 server.

## Setup

- Task: `sclevine__yj.8016400`
- Difficulty: easy
- Agent: `mini-swe-agent`
- Model backend: AWS Bedrock through LiteLLM
- Model: `bedrock/arn:aws:bedrock:us-west-2:497589205881:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- Runtime config: `configs/programbench_mac_smoke.yaml`
- Step limit: 30
- Cost limit: 0.30 in config, observed Bedrock/LiteLLM run costs below
- Oracle spec source: ProgramBench `tests.json`, converted to `reports/oracle_specs/sclevine__yj.8016400.oracle_spec.md`

## Commands

Prepare:

```bash
env -u ANTHROPIC_API_KEY CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2 AWS_PROFILE=default \
  python3 tools/programbench_run_upper_bound.py \
  --stage prepare \
  --tasks sclevine__yj.8016400 \
  --runtime-config configs/programbench_mac_smoke.yaml \
  --model 'bedrock/arn:aws:bedrock:us-west-2:497589205881:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0'
```

Baseline:

```bash
env -u ANTHROPIC_API_KEY CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2 AWS_PROFILE=default \
  python3 tools/programbench_run_upper_bound.py \
  --stage baseline \
  --yes-run-agent \
  --tasks sclevine__yj.8016400 \
  --runtime-config configs/programbench_mac_smoke.yaml \
  --model 'bedrock/arn:aws:bedrock:us-west-2:497589205881:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0'
```

Oracle-spec-assisted:

```bash
env -u ANTHROPIC_API_KEY CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-west-2 AWS_DEFAULT_REGION=us-west-2 AWS_PROFILE=default \
  python3 tools/programbench_run_upper_bound.py \
  --stage oracle \
  --yes-run-agent \
  --tasks sclevine__yj.8016400 \
  --runtime-config configs/programbench_mac_smoke.yaml \
  --model 'bedrock/arn:aws:bedrock:us-west-2:497589205881:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0'
```

Evaluation:

```bash
cd external/ProgramBench
uv run programbench eval /Users/harmin/Desktop/programbench/reports/programbench_upper_bound_runs/baseline \
  --filter '^sclevine__yj\.8016400$' \
  --summarize-only

uv run programbench eval /Users/harmin/Desktop/programbench/reports/programbench_upper_bound_runs/oracle_spec \
  --filter '^sclevine__yj\.8016400$' \
  --summarize-only
```

## Results

### Run 1: Full Oracle-Test Name Spec

| Variant | Agent status | Observed agent cost | ProgramBench score | Comment |
|---|---:|---:|---:|---|
| baseline | Submitted | `$0.26` | 32 | 767 active tests |
| oracle-spec-assisted | LimitsExceeded | `$0.38` | 0 | `compile_failed` |

Detailed comparison:

- `reports/programbench_upper_bound_runs/upper_bound_comparison.md`
- `reports/programbench_upper_bound_runs/baseline/sclevine__yj.8016400/sclevine__yj.8016400.eval.json`
- `reports/programbench_upper_bound_runs/oracle_spec/sclevine__yj.8016400/sclevine__yj.8016400.eval.json`
- `reports/programbench_upper_bound_runs/baseline/sclevine__yj.8016400/sclevine__yj.8016400.traj.json`
- `reports/programbench_upper_bound_runs/oracle_spec/sclevine__yj.8016400/sclevine__yj.8016400.traj.json`

## Observations

Bedrock access is working. Claude Code, LiteLLM, and mini-swe-agent can all call the Bedrock Claude profile from this machine.

The MacBook path is usable for smoke runs, but not ideal for real evaluation. The baseline eval took about 12.5 minutes with `--docker-cpus 2`. Re-running the oracle eval with `--docker-cpus 6` completed quickly because it failed at compile time.

The baseline submitted a compiling implementation and passed a non-trivial subset of tests. Its main missing areas are HCL support, exact flag/error semantics, stdout/stderr/exit-code behavior, and edge cases such as empty stdin and special float handling.

The oracle-spec-assisted run did not establish an upper bound. It received too much raw spec material, spent more budget exploring/implementing, then failed to compile because it used external Go modules without a usable `go.sum`/offline dependency setup:

```text
main.go:12:2: missing go.sum entry for module providing package gopkg.in/yaml.v3
```

This is an important negative result: directly injecting full oracle tests as a prompt is not enough, and can hurt under tight budgets. The useful research problem is therefore not just "give tests to the agent"; it is how to convert tests into compact, implementation-ready, budget-aware specs.

### Run 2: Compact Oracle Spec With Offline-Compile Constraint

After the first negative result, we ran a second oracle-spec variant:

- Oracle spec source: same ProgramBench oracle tests.
- Compression: keep only 4 representative test intents per behavioral group.
- Additional prompt constraint: do not introduce external modules unless vendored; prioritize an offline-compiling submission.
- Runtime config: `configs/programbench_mac_oracle_smoke.yaml`
- Step limit: 45
- Cost limit: 0.60

| Variant | Agent status | Observed agent cost | Official score | Active tests passed |
|---|---:|---:|---:|---:|
| baseline | Submitted | `$0.26` | 32 | 242 / 767 |
| compact oracle-spec-assisted | LimitsExceeded | `$0.60` | 42 | 320 / 767 |

Detailed compact comparison:

- `reports/programbench_upper_bound_runs_compact/upper_bound_comparison.md`
- `reports/programbench_upper_bound_runs_compact/oracle_spec/sclevine__yj.8016400/sclevine__yj.8016400.eval.json`
- `reports/programbench_upper_bound_runs_compact/oracle_spec/sclevine__yj.8016400/sclevine__yj.8016400.traj.json`

This is the first positive signal for Robin's proposed gap measurement: oracle-derived specs can improve an agent on ProgramBench, but only when the spec is compact and operational enough for the agent to act on within budget. The improvement here is roughly +10 points by official rounded ProgramBench score, or +10.17 percentage points by active pass rate.

### Run 3: Compact Oracle Spec + Scaffold-Generated Architecture Plan

We then added a lightweight "spec-to-architecture" scaffold:

- New tool: `tools/programbench_architecture_plan.py`
- Input: oracle-spec JSON generated from ProgramBench `tests.json`
- Output: an implementation plan with behavior priorities, offline-compile constraints, exploration budget, and a submission checklist
- Runtime config: `configs/programbench_mac_arch_plan_smoke.yaml`
- Step limit: 50
- Cost limit: 0.75

| Variant | Agent status | Observed agent cost | Official score | Active tests passed |
|---|---:|---:|---:|---:|
| baseline | Submitted | `$0.26` | 32 | 242 / 767 |
| compact oracle-spec-assisted | LimitsExceeded | `$0.60` | 42 | 320 / 767 |
| compact oracle + architecture plan | LimitsExceeded | `$0.83` | 53 | 405 / 767 |

Detailed architecture-plan comparison:

- `reports/programbench_upper_bound_runs_arch_plan/upper_bound_comparison.md`
- `reports/programbench_upper_bound_runs_arch_plan/oracle_spec/sclevine__yj.8016400/sclevine__yj.8016400.eval.json`
- `reports/programbench_upper_bound_runs_arch_plan/oracle_spec/sclevine__yj.8016400/sclevine__yj.8016400.traj.json`
- `reports/oracle_specs_arch_plan/sclevine__yj.8016400.architecture_plan.md`

Trajectory-level signal:

| Variant | Messages | Commands | External dependency attempts | Compile/build calls |
|---|---:|---:|---:|---:|
| compact oracle-spec-assisted | 81 | 39 | 2 | 6 |
| compact oracle + architecture plan | 59 | 28 | 0 | 2 |

This is the strongest current evidence for the research idea. The scaffold is not merely giving more information; it changes the agent's workflow. The architecture-plan run avoided external dependency attempts, reached a compiling implementation faster, and improved from 242/767 to 405/767 active tests.

## Immediate Next Steps

1. Keep the compact oracle-spec + architecture-plan interface as the main upper-bound scaffold.
   The full oracle-test-name prompt failed to compile, compact spec improved `yj` from 32 to 42, and compact spec + architecture plan improved it to 53.

2. Add implementation constraints to the prompt.
   The architecture plan made this stronger: it reduced external dependency attempts from 2 to 0.

3. Expand to the next dev-set task.
   Run the same comparison on `dsq` next: baseline, compact oracle spec, and compact oracle + architecture plan. This tests whether the positive signal generalizes beyond `yj`.

4. Analyze remaining `yj` failures.
   The architecture-plan variant still misses 362/767 active tests. The next `yj`-specific scaffold should target the largest remaining failure clusters rather than increasing prompt size.

5. Move formal runs to Linux x86-64 when available.
   MacBook Docker/Rosetta is acceptable for smoke tests, but not for multi-task repeated trials.
