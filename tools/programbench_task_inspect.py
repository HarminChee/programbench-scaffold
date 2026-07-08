#!/usr/bin/env python3
"""Inspect ProgramBench task metadata and recommend a small MVP dev set."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


PREFERRED_TERMS = ("figlet", "htmlq", "yj", "jplot", "dsq", "ripsecrets")
PENALTY_TERMS = ("tui", "tmux", "fzf", "htop", "clock", "cmatrix", "lazygit", "nnn")
SYNTHETIC_TASK_IDS = {"testorg__calculator.abc1234"}


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    """Parse the flat scalar fields we need from ProgramBench task.yaml files."""
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


def test_prefix(name: str) -> str:
    parts = name.split(".")
    if len(parts) >= 2:
        return parts[-2]
    return name.split("[", 1)[0]


def inspect_tests(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    branches = payload.get("branches") or {}
    active_branch_ids: list[str] = []
    branch_count = len(branches)
    ignored_branch_count = 0
    listed_tests = 0
    active_tests = 0
    ignored_tests = 0
    prefixes: Counter[str] = Counter()
    sample_tests: list[str] = []

    for branch_id, branch in branches.items():
        if not isinstance(branch, dict):
            continue
        if branch.get("ignored"):
            ignored_branch_count += 1
            continue
        active_branch_ids.append(branch_id)
        tests = [item for item in branch.get("tests") or [] if isinstance(item, str)]
        ignored_names = ignored_test_names(branch)
        listed_tests += len(tests)
        ignored_tests += len(ignored_names)
        for name in tests:
            if name in ignored_names:
                continue
            active_tests += 1
            prefixes[test_prefix(name)] += 1
            if len(sample_tests) < 8:
                sample_tests.append(name)

    return {
        "branch_count": branch_count,
        "active_branch_count": len(active_branch_ids),
        "ignored_branch_count": ignored_branch_count,
        "active_branch_ids": active_branch_ids,
        "listed_tests": listed_tests,
        "active_tests": active_tests,
        "ignored_tests": ignored_tests,
        "top_test_groups": prefixes.most_common(10),
        "sample_tests": sample_tests,
    }


def score_task(task_id: str, meta: dict[str, Any], stats: dict[str, Any]) -> tuple[int, list[str]]:
    text = " ".join([task_id, str(meta.get("repository", "")), str(meta.get("language", ""))]).lower()
    active_tests = int(stats.get("active_tests") or 0)
    difficulty = str(meta.get("difficulty", "")).lower()
    language = str(meta.get("language", "")).lower()
    notes: list[str] = []
    score = 0

    if difficulty == "easy":
        score += 50
        notes.append("easy")
    if any(term in text for term in PREFERRED_TERMS):
        score += 45
        notes.append("requested candidate")
    if language in {"c", "go", "rs", "rust"}:
        score += 8
        notes.append(f"simple build target: {language}")
    if 20 <= active_tests <= 800:
        score += 12
        notes.append("manageable oracle-test count")
    elif active_tests > 2000:
        score -= 12
        notes.append("large test suite")
    elif active_tests == 0:
        score -= 20
        notes.append("no active tests")
    if any(term in text for term in PENALTY_TERMS):
        score -= 20
        notes.append("possible TUI/interactive complexity")
    if stats.get("active_branch_count") == 1:
        score += 4
        notes.append("single active branch")
    return score, notes


def inspect_tasks(tasks_root: Path, *, include_synthetic: bool = False) -> list[dict[str, Any]]:
    rows = []
    for task_dir in sorted(path for path in tasks_root.iterdir() if path.is_dir()):
        if not include_synthetic and task_dir.name in SYNTHETIC_TASK_IDS:
            continue
        task_yaml = task_dir / "task.yaml"
        tests_json = task_dir / "tests.json"
        if not task_yaml.exists() or not tests_json.exists():
            continue
        meta = parse_simple_yaml(task_yaml)
        stats = inspect_tests(tests_json)
        score, notes = score_task(task_dir.name, meta, stats)
        rows.append(
            {
                "task_id": task_dir.name,
                "repository": meta.get("repository", ""),
                "commit": meta.get("commit", ""),
                "language": meta.get("language", ""),
                "difficulty": meta.get("difficulty", ""),
                "score": score,
                "fit_notes": notes,
                **stats,
            }
        )
    return sorted(rows, key=lambda item: (-item["score"], item["task_id"]))


def markdown_table(rows: list[dict[str, Any]], limit: int) -> str:
    lines = [
        "# ProgramBench MVP Task Inspection",
        "",
        "This report ranks official ProgramBench tasks for the parity dev set: easy CLI programs, manageable active test counts, and enough oracle-test signal to reproduce ProgramBench-style generation, filtering, and coverage checks.",
        "",
        "Synthetic fixture tasks such as `testorg__calculator.abc1234` are excluded by default.",
        "",
        "| rank | task | repo | lang | difficulty | active tests | branches | score | notes |",
        "|---:|---|---|---|---|---:|---:|---:|---|",
    ]
    for index, row in enumerate(rows[:limit], start=1):
        notes = "; ".join(row["fit_notes"])
        lines.append(
            "| {rank} | `{task}` | `{repo}` | {lang} | {difficulty} | {tests} | {branches} | {score} | {notes} |".format(
                rank=index,
                task=row["task_id"],
                repo=row["repository"],
                lang=row["language"],
                difficulty=row["difficulty"],
                tests=row["active_tests"],
                branches=row["active_branch_count"],
                score=row["score"],
                notes=notes,
            )
        )

    requested = [row for row in rows if any(term in row["task_id"].lower() or term in row["repository"].lower() for term in PREFERRED_TERMS)]
    lines.extend(
        [
            "",
            "## Requested Candidate Snapshot",
            "",
            "| task | active tests | top oracle-test groups | sample tests |",
            "|---|---:|---|---|",
        ]
    )
    for row in requested:
        groups = ", ".join(f"{name}({count})" for name, count in row["top_test_groups"][:5])
        samples = "<br>".join(f"`{name}`" for name in row["sample_tests"][:4])
        lines.append(f"| `{row['task_id']}` | {row['active_tests']} | {groups} | {samples} |")

    lines.extend(
        [
            "",
            "## Immediate Dev-Set Recommendation",
            "",
            "Start with `sclevine__yj.8016400`, `multiprocessio__dsq.c3ae0ba`, and `rs__jplot.2a54bcc` for ProgramBench-parity oracle reproduction. Keep `sirwart__ripsecrets.34c9e03`, `cmatsuoka__figlet.202a0a8`, and `mgdm__htmlq.6e31bc8` as alternates after the first smoke run.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tasks-root",
        type=Path,
        default=Path("external/ProgramBench/src/programbench/data/tasks"),
    )
    parser.add_argument("--json-out", type=Path, default=Path("reports/programbench_task_inspection.json"))
    parser.add_argument("--md-out", type=Path, default=Path("reports/programbench_task_selection.md"))
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--include-synthetic", action="store_true")
    args = parser.parse_args()

    rows = inspect_tasks(args.tasks_root, include_synthetic=args.include_synthetic)
    payload = {
        "tasks_root": str(args.tasks_root),
        "task_count": len(rows),
        "include_synthetic": args.include_synthetic,
        "excluded_synthetic_task_ids": [] if args.include_synthetic else sorted(SYNTHETIC_TASK_IDS),
        "preferred_terms": list(PREFERRED_TERMS),
        "rows": rows,
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.write_text(markdown_table(rows, args.limit), encoding="utf-8")
    print(json.dumps({"tasks": len(rows), "json_out": str(args.json_out), "md_out": str(args.md_out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
