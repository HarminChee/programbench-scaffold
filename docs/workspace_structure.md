# ProgramBench 研究工作区

当前研究环境以 WSL2 的 ext4 文件系统为主，根目录是
`/home/programbench/research/oracle-workspace`。源码仓库与实验产物分开保存，
这样可以避免模型生成内容污染固定 commit，也能让 Docker、Go 编译和大量小文件
操作保持稳定性能。

```text
/home/programbench/research/
├── programbench/                    # 官方 ProgramBench 仓库
├── programbench-scaffold/           # codex-programbench-gym 分支
└── oracle-workspace/
    ├── config/                       # workflow 参数和模型配置
    ├── repos/                        # 指向两个固定仓库的只读入口
    ├── datasets/                     # task metadata 与 blob 索引
    ├── cache/                        # image、source、依赖缓存
    ├── worktrees/                    # 每个 case 的隔离 agent 工作区
    ├── experiments/
    │   ├── queues/                   # 待运行任务队列
    │   ├── runs/<instance_id>/       # 单实例完整轨迹
    │   └── batches/<batch_id>/       # 批处理 manifest 与汇总
    ├── artifacts/
    │   ├── gold/                     # reference/gold 构建记录
    │   ├── oracles/                  # 候选与最终 oracle suites
    │   ├── coverage/                 # statement、line、gap 证据
    │   └── reviews/                  # 独立 review agent 输出
    ├── manifests/                    # 46 个 Go 实例及运行参数
    ├── logs/                         # 基础设施日志
    ├── docs/                         # 运行期说明
    └── secrets/                      # 只放安全说明，不保存凭证
```

每个实例都使用 `experiments/runs/<instance_id>` 作为唯一运行根。里面的
`agent_workspace` 保存固定源码、reference probe 工具、跨仓库 one-shot 示例和当前
case spec；`iterations` 保存每轮 generation、review 与 pipeline summary；`artifacts`
保存 reference capture、pytest oracle、coverage profile 和 quality report；`final`
只保存最终状态与被接受 suite 的指针。运行中断后，batch runner 根据 final 状态继续
未完成实例，不会覆盖已接受结果。

Windows 侧保留下载包、PortableGit、Claude Code 和进入 WSL 的入口。正式实验不在
NTFS 目录里编译 Go 仓库。Agent Maestro 运行在 Windows VS Code 的 23333 端口，
Claude Code 同时安装在 Windows 与 WSL；模型密钥只经过 VS Code Secret Storage
和进程环境传入，manifest 仅记录凭证来源，不记录凭证内容。

初始化命令如下：

```bash
python setup/initialize_programbench_research_workspace.py
```

全量 Go 任务从官方 `task.yaml` 动态发现。默认批处理先用 1 worker，稳定后升到 3，
最后上限为 5。每个实例都保留独立 stdout、stderr、超时、模型、prompt hash、工具
权限和 coverage 轨迹，因此可以复现实验，也能区分模型失败、测试质量失败与网络
基础设施失败。
