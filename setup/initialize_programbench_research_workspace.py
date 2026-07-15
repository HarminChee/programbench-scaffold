#!/usr/bin/env python3
"""Create the durable ProgramBench research workspace and inventory."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("/home/programbench/research/oracle-workspace")
DEFAULT_PROGRAMBENCH = Path("/home/programbench/research/programbench")
DEFAULT_SCAFFOLD = Path("/home/programbench/research/programbench-scaffold")

DIRECTORIES = [
    "config",
    "repos",
    "datasets/blobs",
    "datasets/task_metadata",
    "cache/images",
    "cache/source",
    "worktrees",
    "experiments/queues",
    "experiments/runs",
    "experiments/batches",
    "artifacts/gold",
    "artifacts/oracles",
    "artifacts/coverage",
    "artifacts/reviews",
    "manifests",
    "logs",
    "docs",
    "secrets",
]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0] if text else None


def parse_language(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("language:"):
            return line.split(":", 1)[1].strip()
    return None


def link_repo(link: Path, target: Path) -> None:
    if link.is_symlink() and link.resolve() == target.resolve():
        return
    if link.exists() or link.is_symlink():
        raise FileExistsError(f"Refusing to replace existing workspace path: {link}")
    link.symlink_to(target, target_is_directory=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--programbench-repo", type=Path, default=DEFAULT_PROGRAMBENCH)
    parser.add_argument("--scaffold-repo", type=Path, default=DEFAULT_SCAFFOLD)
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    for rel in DIRECTORIES:
        (root / rel).mkdir(parents=True, exist_ok=True)
    link_repo(root / "repos" / "programbench", args.programbench_repo.expanduser().resolve())
    link_repo(root / "repos" / "programbench-scaffold", args.scaffold_repo.expanduser().resolve())

    workflow = {
        "schema_version": 2,
        "language": "go",
        "generation": {
            "provider": "claude-code",
            "model": "claude-sonnet-5[1m]",
            "permission_mode": "dontAsk",
            "session_persistence": False,
            "max_turns": 24,
            "max_iterations": 8,
            "max_cases": 2000,
        },
        "review": {"provider": "agent-maestro-anthropic", "model": "claude-opus-4.8", "required": True},
        "coverage": {"target_percent": 85.0, "plateau_delta": 0.5, "plateau_patience": 2},
        "gates": [
            "reference_pass",
            "deterministic_three_runs",
            "dummy_reject",
            "source_leak_scan",
            "assertion_lint",
            "independent_agent_review",
            "coverage_measured",
            "binary_behavior_consistent",
        ],
        "concurrency_ramp": [1, 3, 5],
        "secrets_policy": "environment or VS Code Secret Storage only",
    }
    write_json(root / "config" / "workflow.json", workflow)

    tasks_root = args.programbench_repo / "src" / "programbench" / "data" / "tasks"
    go_tasks = sorted(
        path.parent.name for path in tasks_root.glob("*/task.yaml") if parse_language(path) == "go"
    )
    write_json(
        root / "manifests" / "programbench_go_all.json",
        {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "count": len(go_tasks),
            "instances": go_tasks,
        },
    )
    inventory = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "docker": version(["docker", "--version"]),
        "docker_compose": version(["docker", "compose", "version"]),
        "go": version(["/usr/local/go/bin/go", "version"]),
        "rust": version([str(Path.home() / ".cargo" / "bin" / "rustc"), "--version"]),
        "uv": version([str(Path.home() / ".local" / "bin" / "uv"), "--version"]),
        "claude_code": version([str(Path.home() / ".local" / "bin" / "claude"), "--version"]),
        "programbench_repo": str(args.programbench_repo.resolve()),
        "scaffold_repo": str(args.scaffold_repo.resolve()),
        "go_task_count": len(go_tasks),
    }
    write_json(root / "workspace_inventory.json", inventory)

    (root / "secrets" / "README.md").write_text(
        "# Secrets\n\nDo not write credentials here. Inject Agent Maestro credentials through "
        "VS Code Secret Storage and the process environment at run time.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# ProgramBench Oracle Research Workspace\n\n"
        "This ext4-backed WSL workspace separates immutable repositories, temporary "
        "agent worktrees, cached inputs, experiment manifests, run outputs, and final "
        "artifacts. Run data lives under `experiments/runs/<instance_id>`; accepted "
        "suite pointers live in each run's `final/` directory. Credentials are never "
        "stored in this tree.\n",
        encoding="utf-8",
    )
    print(json.dumps({"root": str(root), "go_task_count": len(go_tasks), "inventory": inventory}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
