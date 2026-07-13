# ProgramBench PB-Style Go Oracle Framework - 2026-07-12

## Summary

The Go oracle path now follows the ProgramBench construction workflow more
closely:

- source-aware builder phase may inspect target source, docs, native tests, and
  source fixtures;
- target official ProgramBench oracle tests remain forbidden for target test
  generation;
- a same-language one-shot example can be packaged from a different Go instance;
- generated candidate cases are captured against the cleanroom reference
  executable;
- unstable reference observations are filtered before evaluation;
- generated pytest oracle bundles pass repeat, dummy rejection, source-leak, and
  assertion-lint gates;
- Go statement coverage is measured through the existing Go coverage harness.

Claude/Agent Maestro integration is implemented but could not be executed in
this environment because `http://127.0.0.1:23333` was not listening, and
`claude` / `copilot` were not present in the WSL PATH.

## New Framework Pieces

- `tools/programbench_generate_source_aware_cli_cases.py`
  - clones the pinned source checkout;
  - scans docs, flags, native Go tests, and `testdata/*_in.*` fixtures;
  - emits capture-ready `--cases-json` without expected outputs.
- `tools/programbench_prepare_pb_style_go_agent_pack.py`
  - builds a PB-style source-aware prompt pack;
  - enforces target/one-shot separation;
  - supports `agent-maestro-anthropic`, `copilot-cli`, and `claude-code`
    provider entry points;
  - parses agent JSON output back into `--cases-json`.
- `tools/programbench_assertion_linter.py`
  - approximates ProgramBench Appendix A.3.5 weak-assertion checks.
- `tools/programbench_run_pb_style_go_oracle_pipeline.py`
  - orchestrates source-aware cases, reference capture, coverage, quality gates,
    and summary reporting.
- `tools/programbench_generate_cli_oracle_bundle.py`
  - now supports `--determinism-reruns`;
  - now skips volatile timestamp-like output by default.
- `tools/programbench_run_generated_oracle_quality_gates.py`
  - now includes assertion lint in the reusable quality gate payload.

## One-Shot Example

Runtime pack:

```text
reports/programbench_pb_style_agent_packs/sclevine__yj.8016400/pb_style_go_v1/
```

Target:

```text
sclevine__yj.8016400
```

Same-language one-shot example:

```text
multiprocessio__dsq.c3ae0ba
```

The pack includes target source/docs/native-test excerpts and a `dsq` oracle
excerpt. It does not include `yj` official oracle tests or target test blobs.

The attempted Agent Maestro call was recorded as unavailable:

```text
reports/programbench_pb_style_agent_packs/sclevine__yj.8016400/pb_style_go_v1/agent_outputs/agent-maestro-anthropic_claude-sonnet-5.error.json
```

Error:

```text
URLError: <urlopen error [Errno 111] Connection refused>
```

## Verification Matrix

All rows used `pb_source_aware_go_v2` and Linux x86-64 Docker cleanroom images.
Coverage is Go statement coverage, not ProgramBench's language-agnostic line
coverage metric.

| instance | candidate cases | kept tests | skipped | generated coverage | native coverage | binary consistent | dummy reject | source leak | assertion lint | repeat |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| `sclevine__yj.8016400` | 159 | 159 | 0 | 78.7% | 76.2% | true | true | true | 0 high | 159 passed |
| `multiprocessio__dsq.c3ae0ba` | 135 | 135 | 0 | 38.4% | 0.0% | true | true | true | 0 high | 135 passed |
| `rs__jplot.2a54bcc` | 27 | 27 | 0 | 10.8% | 0.0% | true | true | true | 0 high | 27 passed |
| `psampaz__go-mod-outdated.bb79367` | 39 | 34 | 5 | 77.8% | 84.7% | true | true | true | 0 high | 34 passed |

## Interpretation

This is a framework milestone, not a claim that the local source-aware miner has
matched ProgramBench official oracle quality. The local miner is deliberately a
deterministic fallback; the intended next quality jump is to run Claude/SWE-agent
against the generated prompt packs and coverage gaps.

Important findings:

- `yj` improved from the previous source-light smoke path to 78.7% after native
  fixture harvesting, slightly above its native Go coverage baseline but below
  the earlier hand-curated 88.8% ProgramBench official baseline.
- `go-mod-outdated` exposed nondeterministic stderr with embedded timestamps.
  The new volatile-output filter removed 5 unstable cases and made the suite
  pass all gates.
- `dsq` and `jplot` pass all gates but need true coverage-guided agent
  iteration to become strong oracle suites.

## Commands

Prepare a PB-style agent pack for `yj` using `dsq` as a one-shot example:

```bash
python3 tools/programbench_prepare_pb_style_go_agent_pack.py prepare sclevine__yj.8016400 \
  --tasks-root /home/harminchee/codex-workspaces/ProgramBench/src/programbench/data/tasks \
  --example-instance-id multiprocessio__dsq.c3ae0ba \
  --pack-label pb_style_go_v1 \
  --overwrite
```

Run the source-aware fallback pipeline:

```bash
python3 tools/programbench_run_pb_style_go_oracle_pipeline.py sclevine__yj.8016400 \
  --tasks-root /home/harminchee/codex-workspaces/ProgramBench/src/programbench/data/tasks \
  --suite-label pb_source_aware_go_v2 \
  --max-cases 220 \
  --work-root /tmp/programbench_pb_style_go_oracle_pipeline \
  --output-root reports/programbench_pb_style_go_oracle_pipeline \
  --generated-output-root reports/programbench_pb_style_generated_oracles \
  --coverage-output-root reports/programbench_pb_style_go_coverage \
  --overwrite
```

When Agent Maestro is running:

```bash
python3 tools/programbench_prepare_pb_style_go_agent_pack.py call-agent \
  --pack-root reports/programbench_pb_style_agent_packs/sclevine__yj.8016400/pb_style_go_v1 \
  --provider agent-maestro-anthropic \
  --model claude-sonnet-5
```

