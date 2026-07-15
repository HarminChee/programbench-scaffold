# ProgramBench-style Oracle Test Gym

本分支实现一个 Go-first、agent-driven 的 PB-style oracle-test generation gym。目标是在不向 generation agent 暴露 target repo 的 PB 官方 oracle tests 的前提下，使用 target source/docs/native tests、execute-only reference binary、不同实例 example、质量门禁和 coverage feedback，独立生成高质量 behavioral tests。

## Workflow

```text
测量 native 与 PB gold-filtered official statement coverage
→ 准备无 target-oracle 泄露的 agent context 和 deterministic seed
→ Sonnet generation agent 更新完整 candidate_cases.json
→ reference capture 生成 pytest behavioral oracle
→ cleanroom/source-built/coverage 三 binary 验证
→ assertion linter + 四 dummy + repeat + source-leak
→ Opus reviewer 对每个 test 给 keep/revise/reject
→ 质量失败则先修质量；质量通过但 statement coverage 不足则按 gaps 补 tests
→ 达到 official statement coverage 后，在新 workspace 独立复验
→ 保存 final suite、coverage、quality、review 和 trajectory artifacts
```

正式成功要求同时满足：我们的 Go statement coverage 达到或超过同一 pinned source 上 PB gold-filtered deterministic official oracle；所有 tests 在三 binary 上通过且行为一致；所有 tests 确定性重复通过；assertion linter、source-leak 和 repeat gates 通过；每个 test 拒绝 `true`、`false`、`cat-stdin`、`empty-stderr` 四种 dummy；reviewer 对所有 tests 均为 `keep`。Plateau 或预算耗尽只保存 best-quality suite，不算正式成功。

## Core scripts

| Script | Responsibility |
|---|---|
| `tools/programbench_agent_oracle_loop.py` | 主控制循环：generation、pipeline、review、coverage feedback、checkpoint 和停止状态 |
| `tools/programbench_agent_provider.py` | Claude Code/Agent Maestro generation 与 review provider |
| `tools/programbench_prepare_pb_style_go_agent_pack.py` | 准备 target source/docs/native tests 和不同实例 example，阻止 target official oracle 泄露 |
| `tools/programbench_generate_source_aware_cli_cases.py` | 从 source、flags、native tests 和 testdata 生成 deterministic seed cases |
| `tools/programbench_agent_probe_reference.py` | Execute-only reference probe，支持 args/stdin/env/files/local HTTP |
| `tools/programbench_agent_probe_reference_windows.ps1` | Windows Agent Maestro 到 WSL reference probe 的安全桥接 |
| `tools/programbench_agent_validate_cases_windows.ps1` | 在 agent 提交 suite 前验证 candidate schema、数量和可执行性 |
| `tools/programbench_generate_cli_oracle_bundle.py` | 捕获 reference stdout/stderr/return code，做 determinism/volatile filtering，并生成 pytest bundle |
| `tools/programbench_go_coverage_harness.py` | 构建 source/coverage binary，运行 native/official/generated suites，测 Go coverage 并比较三 binary |
| `tools/programbench_assertion_linter.py` | PB Table 8/A.3.5 风格弱断言检查 |
| `tools/programbench_run_generated_oracle_quality_gates.py` | 逐 test dummy rejection、linter、repeat 和 source-leak gates |
| `tools/programbench_test_review_agent.py` | 独立 reviewer 对每个 test 输出 keep/revise/reject |
| `tools/programbench_run_pb_style_go_oracle_pipeline.py` | 可复用单轮：capture → coverage/三 binary → quality gates |
| `tools/programbench_run_go_agent_batch.py` | 多 instance 调度、resume、并发限制和基础设施重试 |
| `tools/programbench_summarize_go_agent_runs.py` | 将 run summaries 聚合为 Markdown/CSV/JSON |

## Environment scripts

| Script | Responsibility |
|---|---|
| `setup/programbench_wsl_bootstrap.sh` | 初始化 WSL、Docker、Python 和 Go 研究运行环境 |
| `setup/programbench_wsl_user_setup.sh` | 配置 WSL research user |
| `setup/initialize_programbench_research_workspace.py` | 建立 scaffold、official ProgramBench 和 oracle workspace 目录 |
| `setup/setup_programbench_windows.ps1` | Windows 侧 ProgramBench 基础设置 |
| `setup/initialize_agent_maestro_api_key.ps1` | 从 Windows DPAPI 恢复已有 Maestro key，不把 key 写入仓库/WSL |
| `setup/invoke_agent_maestro_anthropic.ps1` | 调用本机 Maestro Anthropic endpoint |
| `setup/start_claude_via_agent_maestro.ps1` | 启动指定 Sonnet/Opus Claude Code 会话 |
| `setup/run_claude_code_via_agent_maestro_noninteractive.ps1` | 主循环使用的非交互 Claude Code 入口 |

Windows 实验直接连接 `127.0.0.1:23333`。不要关闭 Maestro 鉴权，不生成或轮换 key，不把 key 输出到终端、日志、WSL 或仓库。

## Run one Go instance

先测量并过滤 official ground truth，确定 `TARGET_STATEMENT_COVERAGE`，再运行：

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

## Reproduced results so far

| Instance | Native statement | PB official statement | Generated tests | Our statement | Result |
|---|---:|---:|---:|---:|---|
| `sclevine__yj.8016400` | 76.2% | 88.8% | 143 | 89.7% | target met; 143 keep; 0 dummy-passing |
| `tomnomnom__gron.88a6234` | 70.2% | 93.3% | 100 | 93.5% | target met; 100 keep; 0 dummy-passing; independent 8x capture passed |

Detailed evidence and all 46 Go seed-baseline rows are in [docs/programbench_complete_experiment_results_and_workflow_2026-07-15_zh.md](docs/programbench_complete_experiment_results_and_workflow_2026-07-15_zh.md).

## Answer for Robin: success metrics

Reproducing the ProgramBench pipeline should not be justified by coverage alone. We should report success at four levels. First, **fidelity**: the same pinned repository, commit, cleanroom image, gold executable protocol, and filtering stages are reproduced without exposing the target's official oracle tests to the generation agent. Second, **oracle effectiveness**: on PB repositories, our primary Go metric is statement coverage and the generated suite should reach or exceed the gold-filtered deterministic PB official oracle; executable-line, per-file, and per-function coverage are supporting diagnostics. Third, **oracle quality**: tests must pass on the cleanroom, source-built, and coverage binaries; remain deterministic under independent reruns; reject every dummy implementation; pass assertion-quality and source-leak checks; and receive `keep` decisions from the independent reviewer. Fourth, **efficiency and reproducibility**: report generated and retained test counts, coverage per retained test, agent calls/turns/probes, wall time, failure/filter reasons, and whether a fresh-workspace rerun reproduces the same result. The number of tests is therefore an efficiency/compactness metric, not the primary success criterion. A pipeline is fully successful only when it matches PB-level coverage while satisfying all quality, non-leakage, determinism, and reproducibility gates.

## Tests

```bash
.venv/bin/python -m pytest -q \
  tests/test_programbench_agent_oracle_workflow.py \
  tests/test_programbench_assertion_linter.py \
  tests/test_programbench_cli_fixture_dsl.py
```
