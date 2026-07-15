# ProgramBench Gym Oracle Tests 新版工作流

更新时间：2026 年 7 月 13 日
适用范围：ProgramBench 官方 Go 实例，后续可扩展到其他语言

## 对 ProgramBench 论文的重新解读

ProgramBench 的 oracle test 构建过程由 agent 主导。论文附录 A.3.1 直接说明，研究者先给 SWE-agent 一个或多个 prompt，然后让 agent 写 behavioral tests。coverage-guided iterative 策略里，agent 会阅读程序、源码、已有测试和文档，收集仓库中可复用的行为测试，持续查看 line coverage，再为未覆盖路径补测试。gold 执行失败或 assertion linter 标记的测试也会交回 agent 修改，循环一直运行到覆盖率目标。

最终保留测试仍有一组确定性规则。测试必须在 gold executable 上稳定通过，也必须能拒绝 dummy executable。论文还使用 assertion-quality linter 清理弱断言。这个分工很重要：agent 负责理解行为、提出测试、探索覆盖缺口和修复测试；运行器负责记录事实并决定一个测试是否满足准入条件。我们的新版 Gym 沿用这个分工，同时增加独立 test review agent，降低生成 agent 自我审查时遗漏低质量测试的风险。

论文报告 coverage-guided iterative 的规模明显大于单轮方法，中位数约 750 个测试，多数任务在 200 到 2000 个测试之间，平均 line coverage 为 79.7%，中位数为 86.2%。约 79.5% 的测试由 agent 新生成，20.5% 来自已有测试的 harvesting。这些数字说明 PB 的主要能力来自长循环中的 agent 探索，测试数量、覆盖率和质量反馈需要同时存在。

## 我们的整体流程

新版流程可以用一条主线概括：

```text
固定仓库和 commit
→ 构建隔离 agent 工作区与 gold/reference executable
→ generation agent 阅读源码、文档、native tests 和跨仓库示例
→ generation agent 运行受限 reference probe 并写完整 case spec
→ capture compiler 生成精确 pytest oracle
→ reference、三次确定性、dummy、leak、lint、二进制一致性门禁
→ 独立 review agent 逐项给出 keep / revise / reject
→ Go statement coverage、executable-line coverage、低覆盖函数反馈
→ generation agent 修复并补测
→ 达到覆盖目标或进入有记录的质量平台期
→ 保存最终 suite、轨迹、manifest 和可复现实验结果
```

每轮都要求 generation agent 返回完整 suite。这样可以删除旧测试、合并重复测试，也可以修改已有输入。运行器不会把每轮新增内容盲目累加。每个 case 包含稳定名称、行为类别、参数、stdin、来源和测试理由。expected return code、stdout、stderr 由 cleanroom reference 实际执行后捕获，agent 无法凭空编造输出。

## Generation agent 如何工作

generation agent 使用 Claude Code 运行在单实例隔离目录中。它可以读取固定 commit 的完整源码、README、CLI 文档、native Go tests 和 testdata，也可以读取当前 candidate suite。agent 通过受限的 `probe_reference.py` 调用 execute-only reference，单次 probe 有输入大小、输出大小和超时限制。agent 看不到目标实例的官方 ProgramBench oracle tests，也不能读取目标 hidden blobs。

第一轮先接收 deterministic source-aware miner 生成的 seed。seed 用于提供文档命令、flag、native test 命令和 testdata 输入，generation agent 需要检查这些内容的真实价值，可以保留、修改或删除。随后 agent 按 Args、Config、Help、I/O、Subcommand、TUI 和 error handling 几个行为面探索。仓库没有某一类行为时可以跳过，仓库有明确子命令、配置优先级或格式转换路径时需要展开到具体组合和边界条件。

后续轮次会收到上一轮的门禁结果、review 决策、总覆盖率和低覆盖函数清单。修复顺序固定为：先处理 reference 失败、volatile 输出、lint 高风险和 reviewer 的 revise 项；再删除 reviewer 明确 reject 的测试；最后针对低覆盖函数设计新输入。这个顺序可以防止测试数量增长掩盖已有质量问题。

Claude Code 调用使用 `dontAsk` permission mode、显式工具白名单、外层超时、结构化 JSON schema、最大 turn 数和 `--no-session-persistence`。允许的 shell 范围主要是 `go list`、`go test`、reference probe、`git status` 和 `git diff`。每个 case 使用独立工作区，模型、prompt hash、工具权限、耗时、return code 和 stderr 都写入 manifest。凭证只从运行环境注入。

## Test review agent 放在哪里

独立 review agent 位于确定性门禁之后、coverage 补测之前。它读取 case spec、reference 观察摘要、dummy 结果、repeat 结果、lint 结果、覆盖率和低覆盖函数，不直接编辑文件。reviewer 对每个 case 只能给出 `keep`、`revise` 或 `reject`，并且必须写具体理由。漏审的 case 自动转成 `revise`，整轮无法被接受。

reviewer 重点检查五件事：输入是否对应真实行为，oracle 是否足够具体，测试是否与已有 case 重复，输出是否容易受到时间、随机数、主机路径或网络影响，测试是否只覆盖无价值的帮助信息和通用失败路径。带来独特行为但 coverage 增益很小的测试仍可保留，因为 coverage 只表示执行到某段代码，无法完整衡量行为区分能力。

reviewer 的 reject 会在下一轮交给 generation agent 删除，revise 会带着修改建议返回。最终准入仍由自动门禁执行，reviewer 无权绕过 reference、dummy、determinism 和 leak 检查。实验中会保留 `no-review` ablation，用来测量 reviewer 对测试规模、覆盖率、dummy rejection、稳定性和后续 mutation strength 的实际影响。

## Linter、确定性门禁和 coverage

assertion linter 已按论文 Table 8 做 AST 近似实现，覆盖无断言、恒真断言、单独检查 return code、短 substring、析取断言、try/except 吞错、只检查文件存在、弱长度比较、golden 未比较和 CATCHES 说明不足等规则。HIGH 规则会阻止 suite 进入下一阶段，MED 和 LOW 进入 reviewer 证据与实验统计。

reference capture 对每个 case 重跑三次。return code、stdout 和 stderr 有变化时，该 case 会被标记为 volatile 并移除。时间戳类输出还会经过额外过滤。保留后的 suite 再对 instrumented source binary 重跑，确保 cleanroom、普通 source build 和 coverage build 的测试名称与结果一致。

dummy gate 目前包含成功空输出、stdin 原样回显、固定失败和空 stderr 等实现。suite 必须拒绝每一种 dummy。source-leak scan 检查 agent 可见和最终 oracle 目录，防止源码、构建脚本、仓库路径和目标官方测试材料进入 cleanroom 测试包。

Go harness 同时记录两类覆盖率。Go 工具链原生的 statement coverage 是正式主指标；executable-line coverage 由 Go cover profile 的 statement block 行区间取并集得到，只作为辅助诊断。报告还保留每个文件的可执行行总数、覆盖行数和百分比。低覆盖反馈使用 `go tool cover -func` 提取未覆盖或部分覆盖函数，按覆盖率从低到高交给 generation agent。

循环的 target 应设置为同一 pinned source 上 PB gold-filtered deterministic official oracle 的 statement coverage，最多 8 轮，suite 上限 2000 个 case。连续两轮提升低于 0.5 个百分点时记录 coverage plateau。只有全部质量门禁通过、reviewer 全 keep 且 statement coverage 达到 target，才记录 `accepted_target_coverage`。平台期记录为 `incomplete_quality_plateau`，保存 best-quality suite 供继续迭代，但不算正式成功。

## Example 的使用规则

当前 workflow 已支持 one-shot example。示例必须来自另一个 ProgramBench 实例，语言必须与目标一致。示例包保存 source instance、commit、active branch、archive hash、excerpt hash、目标 instance 和 ablation label。运行器会在准备阶段验证 target 与 example 分离。

example 只展示测试风格、fixture 组织和行为断言方式。generation agent 仍需根据目标源码和 reference probe 发现目标行为。全量实验会同时运行 `with-example` 与 `no-example`，比较覆盖率提升速度、最终测试数、review reject 比例和稳定性，确认示例是否带来真实帮助。

## 工作区和结果组织

正式工作区位于 `/home/programbench/research/oracle-workspace`。官方 ProgramBench 仓库和 scaffold 仓库放在 `repos` 入口，运行时源码与 agent 修改放在 `worktrees` 和每个实例的 `agent_workspace`。下载缓存、测试 blobs、Docker 镜像索引和源码缓存分别保存。每个实例的完整结果位于 `experiments/runs/<instance_id>`，批处理汇总位于 `experiments/batches/<batch_id>`。

单实例结果中，`iterations` 保存每一轮 generation agent、review agent 和 pipeline summary，`artifacts` 保存 oracle、coverage profile 与 review 证据，`final` 保存最终状态和 suite 指针。batch runner 从官方 task metadata 动态发现 46 个 Go 实例，先使用 1 worker，稳定后升到 3，最后最多 5。有限重试只适用于 TLS 传输错误、连接重置和 provider 暂时不可用；测试质量失败、coverage 失败和 reviewer 拒绝会原样保留。

## 当前新电脑上的验证结果

Windows 已安装 Agent Maestro 2.10.0、Claude Code 2.1.207 和 PortableGit 2.55.0.2。WSL2 使用 Ubuntu 22.04，Docker Engine 29.6.1、Go 1.26.5、Python 3.12.13、Rust 1.92.0 和 uv 0.11.28。Go 1.21.13 作为兼容工具链保留，harness 会在固定仓库无法用新工具链链接时自动回退，并记录实际 executable、版本和构建目标。官方 ProgramBench 65 项测试全部通过，cleanroom images 与实验产物按实例缓存。

seed-only 与 no-review 明确标记为 ablation，不计入最终 agent workflow 结论。46 个 Go 仓库的工程基线已经全部形成终态，共产生 4202 个 candidate，3586 个稳定测试进入 oracle，616 个超时、波动、无法启动或不适合执行的 case 被过滤。每个仓库平均保留 78.0 个测试，中位数为 76 个，范围为 2 到 178 个。生成测试的 executable-line coverage 平均为 24.4%，中位数为 20.9%；Go statement coverage 平均为 23.3%，中位数为 19.6%。这一轮用于验证基础设施与反馈闭环，测试规模还没有达到 PB coverage-guided 方法的中位数。

23 个仓库通过 reference、重复执行、四种 dummy、source leak、assertion lint、生成 pytest 和二进制一致性门禁。另 23 个仓库完成了 capture 与 coverage，但生成 pytest 在 instrumented source build 上失败，保留为 `seed_ablation_complete_quality_failed`，其中 12 个还存在 cleanroom、source 与 coverage binary 行为不一致。46 个 suite 都拒绝全部 dummy，也都通过 source leak 和 assertion linter。`yj` 的生成 statement coverage 为 76.8%，`go-mod-outdated` 为 77.8%，Atlas 使用正确的嵌套 `cmd/atlas` 构建后 executable-line coverage 为 2.2%，这些差异为下一轮 agent 提供了清晰的优先级。

Agent Maestro 的 23333 端口仍需要 VS Code 工作区被用户标记为可信后才能启用。端口启用并由用户在当前进程注入本地代理密钥后，正式流程会使用 Sonnet 5 1M 作为 generation agent，Opus 4.8 作为独立 reviewer，先在低覆盖与质量失败仓库上多轮迭代，再扩展到全部 Go 实例。代理密钥不会写入仓库、manifest、日志或本文档。

## 实验判断标准

我们的主结果会比较 PB-style monolithic、decomposed、coverage-guided seed、agent coverage loop、agent loop 加 reviewer、去掉 example 和去掉 reviewer 等设置。每个设置记录最终 test 数、generated 与 harvested 比例、reference pass、dummy rejection、volatile 过滤率、lint 分布、review 分布、statement coverage、executable-line coverage、每轮 coverage delta、运行时间和模型调用量。

workflow 成熟度通过三类证据判断。第一类是复现证据，流程能在固定 commit、离线 cleanroom 和 ProgramBench evaluator 合约下重复运行。第二类是测试强度证据，suite 能拒绝多个 dummy，覆盖目标行为并在后续加入 mutation testing 时杀死更多错误实现。第三类是研究证据，所有 ablation 使用同一批实例、同一门禁和同一预算，能够解释 example、review agent、linter 和 coverage feedback 各自带来的增益。

## 参考资料

ProgramBench 论文与附录 A.3：[arXiv 2605.03546](https://arxiv.org/pdf/2605.03546)
ProgramBench 项目页：[programbench.com](https://programbench.com/)
官方代码：[facebookresearch/programbench](https://github.com/facebookresearch/programbench)
Claude Code CLI 与权限参数：[Claude Code CLI reference](https://code.claude.com/docs/en/cli-usage)
