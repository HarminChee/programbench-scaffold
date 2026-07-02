#!/usr/bin/env python3
"""Convert ProgramBench oracle tests into compact agent-facing spec summaries."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value and not value.startswith("[") and not value.startswith("{"):
            data[key.strip()] = value.strip("'\"")
    return data


def ignored_test_names(branch: dict[str, Any]) -> set[str]:
    names = set()
    for item in branch.get("ignored_tests") or []:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.add(item["name"])
    return names


def split_test_name(name: str) -> tuple[str, str]:
    parts = name.split(".")
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "tests", name


def humanize_test_case(case: str) -> str:
    case = re.sub(r"^test_", "", case)
    case = case.replace("_", " ")
    case = re.sub(r"\[(.*?)\]", r" [\1]", case)
    return case


def collect_active_tests(task_dir: Path) -> dict[str, Any]:
    payload = json.loads((task_dir / "tests.json").read_text(encoding="utf-8"))
    branches = payload.get("branches") or {}
    active_tests: list[dict[str, str]] = []
    ignored_tests = 0
    ignored_branches = 0

    for branch_id, branch in branches.items():
        if not isinstance(branch, dict):
            continue
        if branch.get("ignored"):
            ignored_branches += 1
            continue
        ignored_names = ignored_test_names(branch)
        ignored_tests += len(ignored_names)
        for name in branch.get("tests") or []:
            if not isinstance(name, str) or name in ignored_names:
                continue
            group, case = split_test_name(name)
            active_tests.append(
                {
                    "branch": branch_id,
                    "name": name,
                    "group": group,
                    "case": case,
                    "intent": humanize_test_case(case),
                }
            )

    return {
        "active_tests": active_tests,
        "branch_count": len(branches),
        "ignored_branches": ignored_branches,
        "ignored_tests": ignored_tests,
    }


def oracle_spec(task_id: str, tasks_root: Path, max_per_group: int) -> tuple[dict[str, Any], str]:
    task_dir = tasks_root / task_id
    if not task_dir.exists():
        raise FileNotFoundError(f"Unknown task: {task_id}")
    meta = parse_simple_yaml(task_dir / "task.yaml")
    collected = collect_active_tests(task_dir)
    active_tests = collected["active_tests"]
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in active_tests:
        grouped[item["group"]].append(item)

    group_counts = Counter(item["group"] for item in active_tests)
    payload = {
        "task_id": task_id,
        "repository": meta.get("repository", ""),
        "commit": meta.get("commit", ""),
        "language": meta.get("language", ""),
        "difficulty": meta.get("difficulty", ""),
        "branch_count": collected["branch_count"],
        "ignored_branches": collected["ignored_branches"],
        "active_test_count": len(active_tests),
        "ignored_test_count": collected["ignored_tests"],
        "group_counts": group_counts.most_common(),
        "active_tests": active_tests,
    }

    lines = [
        f"# Oracle Spec Summary: `{task_id}`",
        "",
        f"- Repository: `{payload['repository']}`",
        f"- Commit: `{payload['commit']}`",
        f"- Language: `{payload['language']}`",
        f"- Difficulty: `{payload['difficulty']}`",
        f"- Active oracle tests: {payload['active_test_count']}",
        f"- Branches: {payload['branch_count']} total, {payload['ignored_branches']} ignored",
        f"- Ignored individual tests: {payload['ignored_test_count']}",
        "",
        "## How To Use This Spec",
        "",
        "Treat the bullets below as oracle-derived behavioral requirements. They are intentionally compact: they expose what the official tests check, without copying test source code or outputs.",
        "",
        "## Behavioral Areas",
        "",
    ]
    for group, count in group_counts.most_common():
        lines.append(f"### `{group}` ({count} tests)")
        for item in grouped[group][:max_per_group]:
            lines.append(f"- {item['intent']} (`{item['name']}`)")
        remaining = count - min(count, max_per_group)
        if remaining > 0:
            lines.append(f"- ... {remaining} more oracle tests in this area")
        lines.append("")

    lines.extend(
        [
            "## Agent Instruction Snippet",
            "",
            "Before implementing, cover the behavioral areas above. Preserve exact CLI behavior for arguments, stdin, stdout, stderr, exit codes, file handling, formatting, and documented error cases that the oracle tests imply.",
            "",
        ]
    )
    return payload, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_ids", nargs="+")
    parser.add_argument(
        "--tasks-root",
        type=Path,
        default=Path("external/ProgramBench/src/programbench/data/tasks"),
    )
    parser.add_argument("--out-dir", type=Path, default=Path("reports/oracle_specs"))
    parser.add_argument("--max-per-group", type=int, default=12)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for task_id in args.task_ids:
        payload, markdown = oracle_spec(task_id, args.tasks_root, args.max_per_group)
        json_path = args.out_dir / f"{task_id}.oracle_spec.json"
        md_path = args.out_dir / f"{task_id}.oracle_spec.md"
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        md_path.write_text(markdown, encoding="utf-8")
        summary.append(
            {
                "task_id": task_id,
                "active_tests": payload["active_test_count"],
                "json": str(json_path),
                "markdown": str(md_path),
            }
        )
    print(json.dumps({"generated": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
