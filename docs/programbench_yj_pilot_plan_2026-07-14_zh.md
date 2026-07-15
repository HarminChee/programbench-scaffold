# ProgramBench-style Gym：yj 单实例 Pilot 计划

更新时间：2026-07-14

## 1. 研究方向

本项目首先复现 ProgramBench 从 GitHub repository 构建 gold executable、由 agent 生成 behavioral oracle tests、coverage-guided 补测、质量过滤和 cleanroom 打包的流程。近期从 Go 开始，但架构必须可扩展到 Rust、C/C++ 等其他编译语言。

长期路线是：

`复现标准过滤范式 -> 在原始 ProgramBench 实例上验证 -> 运行我们的新实例 -> 建立外部 GitHub repository 筛选和环境构建 -> 在 ProgramBench 之外生成测试 -> 采样高质量轨迹 -> 用于 rewarding / slimRL 等 RL 训练 -> 改善 agent 的完整程序重建能力并研究跨任务泛化`

## 2. Ground truth 与新增方法

### ProgramBench 论文明确公开的事实

- Oracle 生成使用 agent；agent 可查看目标源码、文档和现有测试，并运行 gold executable。
- Coverage-guided iterative 是论文比较中效果最好的测试生成策略。
- Agent 应 harvesting 已有 executable-level behavioral tests，并针对未覆盖路径持续补测。
- 质量循环先处理 gold execution failure 和 assertion-lint 弱测试，再继续补 coverage。
- 最终丢弃无法在 gold binary 上确定性通过的测试，以及任何能在 dummy binary 上通过的测试。
- Coverage 独立重测 first-party line coverage，排除 system headers、vendored dependencies 和 auto-generated code。
- Appendix A.3.5 Table 8 给出 14 条 HIGH、6 条 MED、1 条 LOW assertion lint 规则。

来源：ProgramBench 官方论文 arXiv 2605.03546，第 2.2、5.1、A.3.1、A.3.2、A.3.5 节；官方代码仓库 `facebookresearch/ProgramBench`。

### 我们明确新增、需要消融验证的方法

- `./example`：只允许使用另一个同语言 ProgramBench 实例的官方 oracle tests 摘录、元数据或 reference behavior，禁止提供目标实例的官方 oracle tests。
- 独立 test-review agent：在确定性证据基础上逐测试给出 `keep/revise/reject`。
- 多种 dummy implementations：用于提供比单一 dummy 更细的弱测试诊断；最终判定仍必须逐测试执行。
- 目标 reference executable 只通过受限 probe 查询，避免读取、反编译或泄漏。

这些是研究变量，不应写成 ProgramBench 官方 workflow 的已知事实。

## 3. yj 固定基线

实例：`sclevine__yj.8016400`

Commit：`80164002c0d7f88aa58fa5bec8a8cf4f1bb4e93b`

| Suite | 测试规模 | Go statement coverage | executable-line coverage | covered / total lines |
|---|---:|---:|---:|---:|
| ProgramBench official oracle | 9 branches；825 references；806 unique names | 88.8% | 88.5% | 759 / 858 |
| Repository native tests | 6 Go test functions | 76.2% | 75.9% | 651 / 858 |

Official oracle 已在 cleanroom、普通 source build、coverage-instrumented build 三种 binary 上验证行为一致。

三种 binary 的定义：

1. Cleanroom binary：官方 `task_cleanroom_v6` image 中的 `/workspace/executable`。
2. Source binary：同一 pinned commit 使用普通 `go build` 构建的 executable。
3. Coverage binary：同一 pinned commit 使用 `go build -cover -coverpkg=./...` 构建的 executable。

旧的 seed-only suite（145 stable cases、76.8% statement）不是正式 agent 结果；其参数化 pytest 结构和 suite-level dummy 判定均不满足当前严格口径。

### 2026-07-14 严格 seed ablation

修正为“一 case 一 test function”和“每个 test 必须拒绝每个 dummy”后，重新运行得到：

| Suite | test functions | statement coverage | executable-line coverage | 三 binary 一致 | dummy-passing tests | 结论 |
|---|---:|---:|---:|---|---:|---|
| Source-aware seed ablation | 151 | 76.8% | 77.0% | 是 | 17 | quality failed |

该 suite 的 gold repeat、source-leak scan 和 Table 8 linter 均通过，但 17 个测试至少会通过一种 dummy，因此必须先修订或删除。它只比 native line coverage 高 1.1 个百分点，距离 official oracle 仍差 11.5 个百分点，不能作为最终 oracle suite。

### 正式 Agent pilot 当前状态

- Windows Agent Maestro 2.10.0 catalog 已恢复；模型调用经 Windows `127.0.0.1:23333`，Linux cleanroom、Docker 和 Go coverage 继续在 WSL 执行。key 只从 Windows DPAPI 读取，不进入 WSL、命令行、日志或仓库。
- 正式接受结果位于 `yj_pb_groundtruth_20260714_v22`：143 个独立 pytest test functions，88.9% executable-line coverage，89.7% statement coverage，143/143 reviewer `keep`，dummy-passing=0，三种 binary 行为一致，状态为 `accepted_target_coverage`。
- 相对 official oracle：line coverage 88.9% vs 88.5%，高 0.4 个百分点；statement coverage 89.7% vs 88.8%，高 0.9 个百分点。生成测试数 143，显著少于 official oracle 的 806 个 unique names，因此该 pilot 证明的是覆盖和当前质量门达到基准，不代表测试多样性已全面等价。
- reviewer 已改为 commit-pinned 语义：固定 commit 的完整 stdout/stderr 是强 oracle；不同输入、解析器或 flag 路径不能仅因输出 hash 相同而判重；temperature=0，分批逐 case fail-closed 审查。
- 质量通过后 generation prompt 切换为有界 coverage-only increment：保留已通过套件、每轮最多新增 16 例、最多 20 次 reference probes，不再反复改写已接受测试。
- orchestration 会保存最高 coverage 且 quality/review 均通过的 checkpoint；最后一轮失败或预算耗尽时不再把之前的优质套件丢成 `final_case_count=0`。
- 当前相关回归测试为 23 项，全部通过。

### 2026-07-14 最终 yj 对照

| Suite | test functions / names | statement coverage | executable-line coverage | dummy-passing | reviewer | 结论 |
|---|---:|---:|---:|---:|---|---|
| Repository native tests | 6 | 76.2% | 75.9% | N/A | N/A | baseline |
| ProgramBench official oracle | 806 unique names | 88.8% | 88.5% | official filtered | official | ground truth |
| Our PB-style agent gym v22 | 143 | 89.7% | 88.9% | 0 | 143 keep / 0 revise / 0 reject | accepted target coverage |

最终产物：`/home/programbench/research/oracle-workspace/pilots/yj_pb_groundtruth_20260714_v22/experiments/runs/sclevine__yj.8016400/final/candidate_cases.json`。

## 4. 正式 yj 实验顺序

1. 固定 repository、commit、Go toolchain、official test blobs、cleanroom image digest 和三种 binary hash。
2. 记录 official oracle 与 native tests 的 test-function 数量和两种 coverage 指标。
3. Generation agent 首轮即以 coverage 为目标：阅读源码、文档、native tests，harvest 行为测试，并通过受限 reference probe 确认行为。
4. Capture engine 为每个 case 生成一个独立 pytest test function，保存 exact stdout、stderr、returncode 和 fixture hashes。
5. 在 gold/coverage binary 上重复运行，过滤 timeout、volatile 和 nondeterministic cases。
6. Table 8 assertion linter 逐 test function 审查；HIGH 必须修复或删除，MED/LOW 进入 reviewer 证据。
7. 每个 test 必须逐一拒绝每种 dummy；任何 dummy-passing test 都进入 revise/reject，而不是用“整个 suite 有失败”替代。
8. Review agent 根据行为独特性、断言强度、重复性和 coverage value 给出 `keep/revise/reject`。
9. 先修复/删除所有 failed 或 weak tests，再针对 first-party executable-line coverage gaps 补测试。
10. 达到 88.5% line coverage、记录质量 plateau，或耗尽 8 轮预算后结束。

## 5. 必须记录的实验字段

- 论文版本、官方仓库 commit、task metadata、test-blob snapshot、Docker image/id。
- Target 和 example instance 的隔离证明；target official oracle 未进入 agent workspace 的扫描证据。
- 每轮完整 prompt hash、模型、provider、tools、return code、耗时和 token usage。
- candidate/stable/filtered test 数；generated/harvested 来源；每个 test 的 rationale、输入和 reference hashes。
- Assertion lint 的逐测试规则和严重度；dummy-passing test names；reviewer 的逐测试判定。
- 三种 binary 的 JUnit test-name hashes 和行为一致性。
- Statement coverage、executable-line coverage、每文件 coverage 和低覆盖函数。
- 最终 suite 指针以及停止原因：target、plateau 或 budget。

## 6. 扩展顺序

只有 yj 的全链路数据和质量口径稳定后，才按同一协议扩到其他 Go 实例；随后抽象 language adapter，依次支持 Rust、C/C++。ProgramBench 内部实例稳定后，再建立外部 GitHub candidate filtering、cleanroom image 构建、sampling 和 RL 数据生产流程。
