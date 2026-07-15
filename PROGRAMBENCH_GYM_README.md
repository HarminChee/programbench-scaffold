# ProgramBench-style Oracle Test Gym

This branch implements a Go-first, agent-driven, PB-style oracle-test generation gym. Its goal is to generate high-quality behavioral tests independently, using the target source, documentation, native tests, an execute-only reference binary, an example from a different instance, deterministic quality gates, and coverage feedback. The target repository's official ProgramBench oracle tests are never exposed to the generation agent.

## Workflow

```text
Measure native and PB gold-filtered official statement coverage
→ Prepare a target-oracle-free agent context and deterministic seed
→ Let the Sonnet generation agent update the complete candidate_cases.json
→ Capture reference behavior and generate a pytest behavioral oracle
→ Validate against cleanroom, source-built, and coverage binaries
→ Run assertion linter, four dummies, repeat, and source-leak gates
→ Let the Opus reviewer mark every test keep, revise, or reject
→ Repair quality failures first; otherwise add tests for coverage gaps
→ Reach the official statement-coverage target
→ Revalidate independently in a fresh workspace
→ Save the final suite, coverage, quality, review, and trajectory artifacts
```

A run is fully successful only when all of the following are true: our Go statement coverage reaches or exceeds the gold-filtered deterministic PB official oracle on the same pinned source; every test passes with consistent behavior on the cleanroom, source-built, and coverage binaries; all tests remain deterministic under reruns; the assertion-linter, source-leak, and repeat gates pass; every test rejects the `true`, `false`, `cat-stdin`, and `empty-stderr` dummy implementations; and the independent reviewer marks every test as `keep`. A plateau or exhausted budget preserves the best-quality suite but does not count as success.

## Core Scripts

| Script | Responsibility |
|---|---|
| `tools/programbench_agent_oracle_loop.py` | Controls the full loop: generation, pipeline execution, review, coverage feedback, checkpoints, and terminal status. |
| `tools/programbench_agent_provider.py` | Provides the Claude Code and Agent Maestro interfaces for generation and review. |
| `tools/programbench_prepare_pb_style_go_agent_pack.py` | Builds the target source/docs/native-test context and different-instance example without leaking the target official oracle. |
| `tools/programbench_generate_source_aware_cli_cases.py` | Generates deterministic seed cases from source code, CLI flags, native tests, and testdata. |
| `tools/programbench_agent_probe_reference.py` | Runs execute-only reference probes with args, stdin, environment variables, files, and local HTTP fixtures. |
| `tools/programbench_agent_probe_reference_windows.ps1` | Bridges Windows Agent Maestro calls to the WSL reference probe safely. |
| `tools/programbench_agent_validate_cases_windows.ps1` | Validates candidate schema, count, uniqueness, and executability before the agent commits a suite. |
| `tools/programbench_generate_cli_oracle_bundle.py` | Captures reference stdout, stderr, and return codes; filters nondeterministic or volatile cases; and generates the pytest bundle. |
| `tools/programbench_go_coverage_harness.py` | Builds source and coverage binaries, runs native/official/generated suites, measures Go coverage, and compares all three binaries. |
| `tools/programbench_assertion_linter.py` | Detects weak assertions using PB Table 8 and Appendix A.3.5-inspired rules. |
| `tools/programbench_run_generated_oracle_quality_gates.py` | Runs per-test dummy rejection, assertion linting, repeat execution, and source-leak gates. |
| `tools/programbench_test_review_agent.py` | Uses an independent reviewer to mark every test as `keep`, `revise`, or `reject`. |
| `tools/programbench_run_pb_style_go_oracle_pipeline.py` | Runs one reusable capture → coverage/three-binary → quality-gate iteration. |
| `tools/programbench_run_go_agent_batch.py` | Schedules multiple instances with resume support, bounded concurrency, and infrastructure retries. |
| `tools/programbench_summarize_go_agent_runs.py` | Aggregates run summaries into Markdown, CSV, and JSON reports. |

## Environment Scripts

| Script | Responsibility |
|---|---|
| `setup/programbench_wsl_bootstrap.sh` | Bootstraps the WSL, Docker, Python, and Go research environment. |
| `setup/programbench_wsl_user_setup.sh` | Configures the WSL research user. |
| `setup/initialize_programbench_research_workspace.py` | Creates the scaffold, official ProgramBench, and oracle-workspace directory layout. |
| `setup/setup_programbench_windows.ps1` | Performs the Windows-side ProgramBench setup. |
| `setup/initialize_agent_maestro_api_key.ps1` | Restores the existing Maestro key from Windows DPAPI without writing it to the repository or WSL. |
| `setup/invoke_agent_maestro_anthropic.ps1` | Calls the local Maestro Anthropic endpoint. |
| `setup/start_claude_via_agent_maestro.ps1` | Starts a Sonnet or Opus Claude Code session through Maestro. |
| `setup/run_claude_code_via_agent_maestro_noninteractive.ps1` | Provides the non-interactive Claude Code entry point used by the main loop. |

Windows experiments connect directly to `127.0.0.1:23333`. Do not disable Maestro authentication, create or rotate keys, or print keys to terminals, logs, WSL, or the repository.

## Run One Go Instance

First measure and filter the official ground truth to determine `TARGET_STATEMENT_COVERAGE`, then run:

```bash
cd /home/programbench/research/programbench-scaffold

.venv/bin/python tools/programbench_agent_oracle_loop.py INSTANCE_ID \
  --tasks-root /home/programbench/research/programbench/src/programbench/data/tasks \
  --workspace-root /home/programbench/research/oracle-workspace/pilots/NEW_RUN \
  --example-instance-id A_DIFFERENT_GO_INSTANCE \
  --generation-model 'claude-sonnet-5[1m]' \
  --review-model 'claude-opus-4.8' \
  --target-coverage TARGET_STATEMENT_COVERAGE \
  --max-iterations 8 \
  --max-turns 24 \
  --overwrite
```

## Reproduced Results So Far

| Instance | Native statement | PB official statement | Generated tests | Our statement | Result |
|---|---:|---:|---:|---:|---|
| `sclevine__yj.8016400` | 76.2% | 88.8% | 143 | 89.7% | Target met; 143 keep; 0 dummy-passing tests. |
| `tomnomnom__gron.88a6234` | 70.2% | 93.3% | 100 | 93.5% | Target met; 100 keep; 0 dummy-passing tests; independent 8x capture passed. |

Detailed evidence and all 46 Go seed-baseline rows are available in [docs/programbench_complete_experiment_results_and_workflow_2026-07-15_zh.md](docs/programbench_complete_experiment_results_and_workflow_2026-07-15_zh.md).

## Success Metrics

Reproducing the ProgramBench pipeline should not be justified by coverage alone. Success should be reported at four levels. First, **fidelity**: reproduce the same pinned repository, commit, cleanroom image, gold-executable protocol, and filtering stages without exposing the target's official oracle tests to the generation agent. Second, **oracle effectiveness**: on PB repositories, use statement coverage as the primary Go metric and require the generated suite to reach or exceed the gold-filtered deterministic PB official oracle; executable-line, per-file, and per-function coverage remain supporting diagnostics. Third, **oracle quality**: require tests to pass on the cleanroom, source-built, and coverage binaries; remain deterministic under independent reruns; reject every dummy implementation; pass assertion-quality and source-leak checks; and receive `keep` decisions from the independent reviewer. Fourth, **efficiency and reproducibility**: report generated and retained test counts, coverage per retained test, agent calls, turns, probes, wall time, failure and filtering reasons, and whether a fresh-workspace rerun reproduces the same result. Test count is therefore an efficiency and compactness metric, not the primary success criterion. A pipeline is fully successful only when it matches PB-level coverage while satisfying all quality, non-leakage, determinism, and reproducibility gates.

## Tests

```bash
.venv/bin/python -m pytest -q \
  tests/test_programbench_agent_oracle_workflow.py \
  tests/test_programbench_assertion_linter.py \
  tests/test_programbench_cli_fixture_dsl.py
```
