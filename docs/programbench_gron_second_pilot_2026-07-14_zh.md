# ProgramBench Go 第二个正式 pilot：tomnomnom/gron

日期：2026-07-14
实例：`tomnomnom__gron.88a6234`
commit：`88a6234ea2d0c487090988182ad9a7cdf6def924`

## 1. 结论

在不向生成 agent 暴露 gron 官方 ProgramBench oracle tests 的前提下，当前 PB-style gym 生成了 100 个确定性 black-box pytest test functions，并达到：

| Suite | Test functions | Go executable-line | Go statement | 三 binary 一致 | Reviewer |
|---|---:|---:|---:|---|---|
| Repo native tests | 原生 Go tests | 70.9% | 70.2% | N/A | N/A |
| PB 官方 oracle（gold-filtered） | 220 | 92.9% | 93.3% | 是 | 官方基线 |
| 我们的 gym final | 100 | 92.9% | 93.5% | 是 | 100 keep / 0 revise / 0 reject |

最终状态：`accepted_target_coverage`。我们的 executable-line coverage 与筛选后的 PB 官方 oracle 相同，statement coverage 高 0.2pp；test function 数约为官方的 45.5%。

最终候选：

`/home/programbench/research/oracle-workspace/pilots/gron_agent_20260714_v6_reject_fix/experiments/runs/tomnomnom__gron.88a6234/final/candidate_cases.json`

最终 run summary：

`/home/programbench/research/oracle-workspace/pilots/gron_agent_20260714_v6_reject_fix/experiments/runs/tomnomnom__gron.88a6234/final/run_summary.json`

## 2. 官方与 native ground truth

官方 oracle 原始集合中有 4 个在 gold/reference binary 上失败的 URL tests，因此按 gold-pass filtering 原则 deselect：

- `test_fetch_url_with_fragment`
- `test_fetch_url_with_userinfo`
- `test_timeout_on_slow_response`
- `test_very_long_url_path`

另有 9 个由官方 metadata 标记为 ignored 的 tests。过滤后有效官方集合为 220 tests，coverage 为 92.9% line / 93.3% statement。移除上述 4 个 gold-failing tests 不改变 coverage，说明它们没有独占 coverage。

官方 filtered baseline：

`/home/programbench/research/oracle-workspace/pilots/gron_pb_groundtruth_20260714_v4_filtered`

## 3. 泄漏边界

- target gron 官方 oracle tests 从未进入 generation agent workspace。
- agent 输入仅包括 target source/docs/native tests、execute-only reference binary、coverage gaps，以及不同实例 yj 的 one-shot style example。
- one-shot example：`sclevine__yj.8016400`。
- 官方 gron oracle 只由研究侧用于独立 ground-truth coverage 统计和 gold filtering，不用于提示或人工抄写 target cases。

## 4. 最终质量门禁

- cleanroom binary、source-built binary、coverage binary：行为一致。
- assertion linter：high=0、medium=0、low=0。
- dummy survivors：0；所有 dummy 均被每个 test 拒绝。
- repeat run：return code 0。
- source leak scan：passed。
- independent Opus reviewer：100 keep / 0 revise / 0 reject。
- 已删除已证实不确定的 `flag_no_sort`：Go map iteration 会在不同 build/run 间改变输出行顺序。

独立 final validation 额外使用 8 次 reference determinism capture，再重跑三 binary 和全部质量门禁，结果仍为 100 cases、92.9% line / 93.5% statement、binary consistent。

独立复核目录：

`/home/programbench/research/oracle-workspace/pilots/gron_final_validation_20260714`

## 5. 关键迭代轨迹

### v1：初始生成

- 50 cases，74.3% coverage。
- 3 dummy survivors；reviewer 发现弱项。
- 第二轮 agent 用满 60 turns 且未提交有效结果。

### v2/v3：分阶段 loop

- 修正 nested feedback 路由。
- 质量修复与 coverage-only 分离：先修 reject/revise/dummy，再补 coverage。
- coverage-only 每轮保留强 suite，最多新增 16 cases、最多 20 probes。
- coverage 从 74.3% 逐步提升到 91.2%。
- 加入 max-turn candidate recovery：只对 `error_max_turns` 的落盘候选执行完整门禁，不对 timeout/transport error 放宽。

### v4/v5：DSL 与失败反馈

- candidate DSL 新增安全 `env`、临时 `files`、loopback `http` fixture，以及 `{http_url}` substitution。
- fixture capture 与生成后的 pytest 共用相同语义；阻止 secret/token/key、`LD_*` 和路径穿越。
- 新增 `binary_inconsistent_case_names` feedback，修复“quality=false 但 agent 不知道具体失败 case”的问题。
- 新增完整行多重集合比较模式 `stdout_mode=lines_unordered`，用于语义上无序但必须完整相等的输出；不是 substring 弱断言。
- 最终 gron suite 没有依赖 env/files/http/unordered mode；这些能力作为后续 repo 的通用基础设施保留。

### v6：review 聚合修复与达标

- 修复 reviewer batch merge：任何 reject 都不能被错误聚合为 suite `keep`。
- 删除 nondeterministic `flag_no_sort`，保留其余 coverage 增量。
- 95 cases / 92.0% → 99 / 92.7% → 100 / 92.9%。

## 6. 本轮发现并修复的 workflow 问题

1. agent 达到 max turns 后，已写出的有效候选被无条件丢弃。
2. quality/review feedback 的实际 nested shape 读取错误。
3. quality repair 与 coverage expansion 混在同一轮，导致 suite 膨胀和 turns 浪费。
4. 三 binary 失败 case 未反馈给 agent。
5. reviewer 有 reject 时，batch aggregate 仍可能错误返回 keep。
6. 单次三 binary batch 可能碰巧掩盖 Go map-order nondeterminism；final validation 需增加多次 determinism capture。
7. 原 candidate DSL 只能表达 args+stdin，无法对齐任意 pytest fixture 的能力；已加入安全 fixture DSL。

对应回归测试当前为 30 passed，包括 max-turn recovery、nested feedback、fixture DSL、unordered full-line equality、binary failure extraction 和 reject merge fail-closed。

## 7. 其他 Go repo screening

- `multiprocessio__dsq.c3ae0ba`：官方 oracle 自身包含依赖 Go map key order 的 exact-output test；同一 test 对三 binary 多次运行不稳定，因此不进入正式生成对比。
- `psampaz__go-mod-outdated.bb79367`：official/native 均为 100%，缺少可用于验证 coverage improvement 的空间。
- `rs__jplot.2a54bcc`：TUI/HTTP/graphics failures 与 timeout 较多，当前不作为第二个 clean pilot。

因此 gron 是 yj 之后第一个满足 gold-filtered、三 binary 一致、存在显著 native→oracle coverage 提升空间的正式第二 pilot。

## 8. 下一步

1. 将 reviewer checkpoint 决策随 resume 携带：平时只审新增/变更 cases，周期性做全量 audit，降低 LLM reviewer 方差和重复成本。
2. 把 final determinism repeats 参数化为正式 acceptance gate，而不是独立事后命令。
3. 在下一个 Go repo 上复用 yj/gron workflow，优先选择官方 oracle gold-pass、三 binary 稳定且 native coverage 明显低于 official 的实例。
4. 为 fixture DSL 增加真实 repo 使用案例和 ablation：args+stdin only vs. files/env/http。
5. 稳定多个 Go pilot 后，再扩展 C++/Rust 的 coverage adapter、cleanroom build 与测试生成。
