# ProgramBench PB-Style Go One-Shot Example Pack

This directory documents the controlled one-shot example used by the
source-aware Go oracle-generation pipeline.

The target task's official ProgramBench oracle tests must never be shown to the
agent. For `sclevine__yj.8016400`, the default one-shot example is the separate
Go instance `multiprocessio__dsq.c3ae0ba`.

Runtime prompt packs are generated under:

```text
reports/programbench_pb_style_agent_packs/<target_instance>/<pack_label>/
```

The pack may include:

- target `task.yaml` metadata;
- target source/docs/native-test excerpts;
- target source inventory;
- one-shot metadata from a different Go task;
- optional one-shot official oracle excerpts from that different task.

The pack intentionally excludes target `tests.json`, target official test
blobs, and any target ProgramBench oracle material.

Example:

```bash
python3 tools/programbench_prepare_pb_style_go_agent_pack.py prepare sclevine__yj.8016400 \
  --tasks-root /home/harminchee/codex-workspaces/ProgramBench/src/programbench/data/tasks \
  --example-instance-id multiprocessio__dsq.c3ae0ba \
  --pack-label pb_style_go_v1 \
  --overwrite
```

When a Claude/Agent Maestro route is available:

```bash
python3 tools/programbench_prepare_pb_style_go_agent_pack.py call-agent \
  --pack-root reports/programbench_pb_style_agent_packs/sclevine__yj.8016400/pb_style_go_v1 \
  --provider agent-maestro-anthropic \
  --model claude-sonnet-5
```

Then convert the model output into a capture-ready case spec:

```bash
python3 tools/programbench_prepare_pb_style_go_agent_pack.py extract-cases \
  --agent-output reports/programbench_pb_style_agent_packs/sclevine__yj.8016400/pb_style_go_v1/agent_outputs/agent-maestro-anthropic_claude-sonnet-5.txt \
  --output-json reports/programbench_pb_style_agent_cases/sclevine__yj.8016400/pb_agent_source_aware_go_v1/cases.json
```

