#!/usr/bin/env python3
"""Run the ProgramBench baseline vs oracle-spec upper-bound experiment."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path


DEFAULT_TASKS = [
    "sclevine__yj.8016400",
    "multiprocessio__dsq.c3ae0ba",
    "rs__jplot.2a54bcc",
]

DEFAULT_UV_WITH = ["mini-swe-agent", "boto3"]


def run(cmd: list[str], cwd: Path, *, execute: bool) -> int:
    print(shlex.join(cmd))
    if not execute:
        return 0
    return subprocess.run(cmd, cwd=cwd, check=False).returncode


def uv_run_prefix(packages: list[str]) -> list[str]:
    cmd = ["uv", "run"]
    for package in packages:
        cmd.extend(["--with", package])
    return cmd


def mini_base_config(programbench_repo: Path, uv_with: list[str]) -> Path:
    cmd = [
        *uv_run_prefix(uv_with),
        "python",
        "-c",
        "from minisweagent.config import builtin_config_dir; print(builtin_config_dir / 'benchmarks' / 'programbench.yaml')",
    ]
    proc = subprocess.run(cmd, cwd=programbench_repo, check=True, capture_output=True, text=True)
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip().endswith("programbench.yaml")]
    if not lines:
        raise RuntimeError(f"Could not locate mini-swe-agent ProgramBench config. Output:\n{proc.stdout}")
    return Path(lines[-1])


def exact_filter(task_id: str) -> str:
    return "^" + task_id.replace(".", r"\.") + "$"


def build_commands(args: argparse.Namespace, base_config: Path) -> dict[str, list[list[str]]]:
    root = args.workspace_root.resolve()
    programbench_repo = args.programbench_repo.resolve()
    output_root = args.output_root.resolve()
    runtime_config = args.runtime_config.resolve()
    tools_dir = root / "tools"
    oracle_dir = root / "reports" / "oracle_specs"
    tasks_root = programbench_repo / "src" / "programbench" / "data" / "tasks"

    commands: dict[str, list[list[str]]] = {"prepare": [], "baseline": [], "oracle": [], "eval": []}

    for task_id in args.tasks:
        oracle_spec = oracle_dir / f"{task_id}.oracle_spec.md"
        oracle_config = oracle_dir / f"{task_id}.mini_swe_oracle_config.yaml"
        commands["prepare"].append(["uv", "run", "programbench", "blob", "sync", task_id])
        commands["prepare"].append(
            [
                "python3",
                str(tools_dir / "programbench_oracle_specs.py"),
                task_id,
                "--tasks-root",
                str(tasks_root),
                "--out-dir",
                str(oracle_dir),
            ]
        )
        commands["prepare"].append(
            [
                *uv_run_prefix(args.uv_with),
                "python",
                str(tools_dir / "programbench_make_oracle_config.py"),
                "--task-id",
                task_id,
                "--oracle-spec",
                str(oracle_spec),
                "--out",
                str(oracle_config),
            ]
        )

        common_agent = [
            *uv_run_prefix(args.uv_with),
            "mini-extra",
            "programbench",
            "--filter",
            exact_filter(task_id),
            "--workers",
            str(args.workers),
            "--model",
            args.model,
            "--config",
            str(base_config),
        ]
        commands["baseline"].append(
            [
                *common_agent,
                "--config",
                str(runtime_config),
                "--output",
                str(output_root / "baseline"),
                "--redo-existing",
            ]
        )
        commands["oracle"].append(
            [
                *common_agent,
                "--config",
                str(oracle_config),
                "--config",
                str(runtime_config),
                "--output",
                str(output_root / "oracle_spec"),
                "--redo-existing",
            ]
        )

        for variant in ("baseline", "oracle_spec"):
            commands["eval"].append(
                [
                    "uv",
                    "run",
                    "programbench",
                    "eval",
                    str(output_root / variant),
                    "--filter",
                    exact_filter(task_id),
                    "--workers",
                    "1",
                    "--branch-workers",
                    "1",
                    "--docker-cpus",
                    str(args.docker_cpus),
                    "--force",
                ]
            )

    return commands


def write_command_script(path: Path, commands: dict[str, list[list[str]]], programbench_repo: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"cd {shlex.quote(str(programbench_repo.resolve()))}",
        "",
    ]
    for stage in ("prepare", "baseline", "oracle", "eval"):
        lines.append(f"# {stage}")
        for cmd in commands[stage]:
            lines.append(shlex.join(cmd))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--programbench-repo", type=Path, default=Path("external/ProgramBench"))
    parser.add_argument("--tasks", nargs="+", default=DEFAULT_TASKS)
    parser.add_argument("--model", default="anthropic/claude-3-5-haiku-20241022")
    parser.add_argument(
        "--uv-with",
        action="append",
        default=[],
        help="Extra packages to add to uv run. Defaults to mini-swe-agent and boto3.",
    )
    parser.add_argument("--output-root", type=Path, default=Path("reports/programbench_upper_bound_runs"))
    parser.add_argument(
        "--runtime-config",
        type=Path,
        default=Path("configs/programbench_mac_smoke.yaml"),
        help="Resource/runtime config merged into mini-swe-agent ProgramBench config.",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--docker-cpus", type=int, default=2)
    parser.add_argument(
        "--stage",
        choices=["commands", "prepare", "baseline", "oracle", "agent", "eval", "all"],
        default="commands",
    )
    parser.add_argument(
        "--yes-run-agent",
        action="store_true",
        help="Required for baseline/oracle/agent/all stages because they spend model quota.",
    )
    args = parser.parse_args()

    root = args.workspace_root.resolve()
    programbench_repo = (root / args.programbench_repo).resolve() if not args.programbench_repo.is_absolute() else args.programbench_repo.resolve()
    args.programbench_repo = programbench_repo
    args.output_root = (root / args.output_root).resolve() if not args.output_root.is_absolute() else args.output_root.resolve()
    args.runtime_config = (
        (root / args.runtime_config).resolve()
        if not args.runtime_config.is_absolute()
        else args.runtime_config.resolve()
    )

    args.uv_with = args.uv_with or DEFAULT_UV_WITH
    base_config = mini_base_config(programbench_repo, args.uv_with)
    commands = build_commands(args, base_config)
    command_script = args.output_root / "run_commands.sh"
    write_command_script(command_script, commands, programbench_repo)
    metadata = {
        "tasks": args.tasks,
        "model": args.model,
        "uv_with": args.uv_with,
        "base_config": str(base_config),
        "runtime_config": str(args.runtime_config),
        "output_root": str(args.output_root),
        "command_script": str(command_script),
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2))

    if args.stage == "commands":
        return 0
    stages = ["prepare", "baseline", "oracle", "eval"] if args.stage == "all" else [args.stage]
    if args.stage == "agent":
        stages = ["baseline", "oracle"]
    if any(stage in {"baseline", "oracle"} for stage in stages) and not args.yes_run_agent:
        raise SystemExit("Refusing to spend model quota without --yes-run-agent.")

    rc = 0
    for stage in stages:
        for cmd in commands[stage]:
            rc = run(cmd, programbench_repo, execute=True)
            if rc != 0:
                return rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
