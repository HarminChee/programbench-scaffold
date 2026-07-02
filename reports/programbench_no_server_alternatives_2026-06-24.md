# ProgramBench No-Server Alternatives - 2026-06-24

## Short Answer

Without a personal Linux x86-64 server, the best next path is to use GitHub Actions as a temporary Linux x86-64 runner.

Use order:

1. GitHub Actions `ubuntu-latest` for one-task `yj` baseline/oracle smoke.
2. If that works, run `yj dsq jplot`.
3. If GitHub Actions is too slow or hits disk/time limits, rent a small cloud VM for one day.
4. Continue local Mac work only for scaffold code, oracle-spec generation, and toy probing.

## Option A: GitHub Actions Hosted Runner

Why it fits:

- No separate server setup.
- Provides Linux x86-64 hosted runners.
- Can run Docker and ProgramBench images.
- Can store model API keys as repository secrets.
- Produces downloadable artifacts with trajectories, eval JSON, and comparison reports.

What I added:

- `.github/workflows/programbench-upper-bound.yml`

How to use:

1. Put this workspace in a GitHub repo.
2. In repo settings, add one of these secrets:
   - `ANTHROPIC_API_KEY`
   - `OPENAI_API_KEY`
   - `OPENROUTER_API_KEY`
3. Go to Actions -> ProgramBench Upper-Bound Experiment -> Run workflow.
4. First run with:
   - `task_ids`: `sclevine__yj.8016400`
   - `stage`: `all`
5. Download `programbench-upper-bound-reports` artifact.

If `yj` works, rerun with:

```text
sclevine__yj.8016400 multiprocessio__dsq.c3ae0ba rs__jplot.2a54bcc
```

## Option B: Keep Using Mac, But Only For Prep

Mac can do:

- Generate oracle specs.
- Generate mini-swe oracle configs.
- Inspect cleanroom files/help behavior.
- Run tiny behavior-probe scaffolds.

Mac should not be used for the actual baseline/oracle agent experiment:

- `yj` cleanroom startup under amd64 emulation took about 132 seconds.
- ProgramBench images and eval containers are too slow for repeated agent/eval runs.
- The current Anthropic key also has insufficient credits.

## Option C: One-Day Cloud VM

If GitHub Actions fails due to time/disk limits, rent a temporary Linux x86-64 VM.

Minimum practical shape:

- Ubuntu 22.04/24.04 x86-64
- 4 vCPU
- 16 GB RAM
- 80 GB disk
- Docker
- `uv`

Then run:

```bash
python3 tools/programbench_run_upper_bound.py --stage prepare --runtime-config configs/programbench_ci_smoke.yaml
python3 tools/programbench_run_upper_bound.py --stage agent --runtime-config configs/programbench_ci_smoke.yaml --yes-run-agent
python3 tools/programbench_run_upper_bound.py --stage eval --runtime-config configs/programbench_ci_smoke.yaml
python3 tools/programbench_compare_upper_bound.py
```

## Current Blocking Conditions

- No Linux x86-64 server currently available.
- Current Anthropic API key returns `credit balance is too low`.

So even GitHub Actions still needs a working model key.

## What We Can Still Do Immediately

- Push or package this workspace for GitHub Actions.
- Run `prepare` stage without model calls.
- Improve oracle-spec compression so prompts are shorter.
- Add trajectory comparison templates and failure taxonomy.
- Run black-box behavior probes locally on small commands or one cleanroom command at a time.
