#!/usr/bin/env python3
"""Analyze ProgramBench agent trajectories across baseline/scaffold variants."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_TASKS = [
    "sclevine__yj.8016400",
    "multiprocessio__dsq.c3ae0ba",
    "rs__jplot.2a54bcc",
]

DEFAULT_VARIANTS = ["baseline", "oracle_spec"]

CATEGORY_PATTERNS = {
    "flags/help/usage": r"flag|option|argparse|usage|help|version|invalid",
    "stdin/file/io": r"stdin|file|path|directory|input|read|write",
    "stdout/stderr/exit": r"stdout|stderr|exit|returncode|return code|error message",
    "format/query/parsing": r"json|yaml|toml|hcl|html|selector|format|convert|query|sql|csv|regex|secret|font|figlet",
    "edge/order/unicode": r"edge|empty|null|unicode|escape|nested|large|order|preserve|special|binary",
    "runtime/build": r"compile|build|timeout|panic|exception|not_run|system_error|failed",
}

COMMAND_PATTERNS = {
    "reference_runs": r"(^|\s)(\./executable|/workspace/executable)(\s|$)",
    "help_docs": r"(--help|\s-h\b|README|LICENSE|cat\s+.*README|sed\s+-n\s+.*README|ls\s+-la)",
    "invalid_error_probes": r"(invalid|bad|unknown|does-not-exist|missing|malformed|error|no-such)",
    "stdin_probes": r"(printf|echo|cat\s+<<|here-doc|\|\s*\./executable|<\s*[A-Za-z0-9_./-]+)",
    "file_probes": r"(touch|mkdir|mktemp|\.json|\.ya?ml|\.toml|\.csv|\.html|\.txt|\.flf|\.rs|\.go|\.py|\.c)",
    "dependency_attempts": r"(go\s+get|go\s+mod\s+tidy|go\s+mod\s+download|cargo\s+add|cargo\s+fetch|pip\s+install|npm\s+install|apt-get|curl\s+https?://|wget\s+https?://|github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|modernc\.org/[A-Za-z0-9_.-]+|go-sqlite3|tablewriter|Cargo\.toml)",
    "compile_calls": r"(\./compile\.sh|go\s+build|cargo\s+build|gcc\s+|clang\s+|make\b|python3\s+-m\s+py_compile)",
    "implementation_edits": r"(cat\s+>|cat\s+<<|tee\s+|sed\s+-i|python3\s+- <<|apply_patch|chmod\s+\+x\s+compile\.sh|touch\s+compile\.sh)",
}

ROUTE_PATTERNS = {
    "python": r"(\.py\b|python3|#!/usr/bin/env python|sqlite3|argparse)",
    "go": r"(\.go\b|go\s+build|go\s+mod|package\s+main)",
    "rust": r"(\.rs\b|Cargo\.toml|cargo\s+build|rustc)",
    "c": r"(\.c\b|gcc\s+|clang\s+|#include)",
    "shell": r"(#!/usr/bin/env bash|#!/bin/sh|\.sh\b)",
}


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_task_scope(tasks_root: Path, task_id: str) -> dict[str, Any]:
    tests_path = tasks_root / task_id / "tests.json"
    if not tests_path.exists():
        return {"active_branches": None, "ignored_tests": set()}
    tests = json.loads(tests_path.read_text(encoding="utf-8"))
    branches = tests.get("branches") or {}
    active_branches = {name for name, info in branches.items() if not info.get("ignored")}
    ignored_tests = {
        f"{branch}/{test['name'] if isinstance(test, dict) else test}"
        for branch, info in branches.items()
        for test in (info.get("ignored_tests") or [])
    }
    return {"active_branches": active_branches, "ignored_tests": ignored_tests}


def scoped_results(payload: dict[str, Any], scope: dict[str, Any]) -> list[dict[str, Any]]:
    active_branches = scope.get("active_branches")
    ignored_tests = scope.get("ignored_tests") or set()
    results = []
    for item in payload.get("test_results") or []:
        branch = item.get("branch") or ""
        if active_branches is not None and branch and branch not in active_branches:
            continue
        full_name = f"{branch}/{item.get('name')}" if branch else str(item.get("name"))
        if full_name in ignored_tests:
            continue
        results.append(item)
    return results


def eval_summary(path: Path, scope: dict[str, Any]) -> dict[str, Any]:
    payload = load_json(path)
    if payload is None:
        return {
            "eval_exists": False,
            "score": None,
            "passed": 0,
            "total": 0,
            "error_code": "missing_eval_json",
            "failure_categories": {"missing_eval_json": 1},
        }
    results = scoped_results(payload, scope)
    passed = sum(1 for item in results if item.get("status") == "passed")
    failure_categories: Counter[str] = Counter()
    if payload.get("error_code"):
        failure_categories["runtime/build"] += 1
    for item in results:
        if item.get("status") in {"passed", "skipped"}:
            continue
        text = " ".join(
            [
                str(item.get("name", "")),
                str(item.get("status", "")),
                str((item.get("extra") or {}).get("message", "")),
                str((item.get("extra") or {}).get("text", "")),
            ]
        ).lower()
        matched = False
        for category, pattern in CATEGORY_PATTERNS.items():
            if re.search(pattern, text):
                failure_categories[category] += 1
                matched = True
        if not matched:
            failure_categories["other"] += 1
    total = len(results)
    return {
        "eval_exists": True,
        "score": round((passed / total) * 100, 2) if total else 0.0,
        "passed": passed,
        "total": total,
        "error_code": payload.get("error_code"),
        "failure_categories": dict(failure_categories),
    }


def extract_commands(messages: list[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    for message in messages:
        extra = message.get("extra") or {}
        for action in extra.get("actions") or []:
            command = action.get("command")
            if isinstance(command, str):
                commands.append(command)
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if function.get("name") != "bash":
                continue
            args = function.get("arguments")
            if not isinstance(args, str):
                continue
            try:
                payload = json.loads(args)
            except json.JSONDecodeError:
                continue
            command = payload.get("command")
            if isinstance(command, str) and command not in commands:
                commands.append(command)
    return commands


def assistant_text(messages: list[dict[str, Any]]) -> str:
    chunks = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
    return "\n".join(chunks)


def infer_route(commands: list[str], text: str) -> str:
    haystack = "\n".join(commands) + "\n" + text
    counts = {
        name: len(re.findall(pattern, haystack, flags=re.IGNORECASE))
        for name, pattern in ROUTE_PATTERNS.items()
    }
    if not any(counts.values()):
        return "unknown"
    return max(counts.items(), key=lambda item: item[1])[0]


def infer_route_sequence(commands: list[str], text: str) -> str:
    sequence: list[str] = []
    for command in commands:
        if not re.search(COMMAND_PATTERNS["implementation_edits"], command, re.IGNORECASE):
            continue
        route = infer_route([command], "")
        if route != "unknown" and (not sequence or sequence[-1] != route):
            sequence.append(route)
    if len(sequence) > 1:
        return "->".join(sequence)
    if len(sequence) == 1:
        return sequence[0]
    return infer_route(commands, text)


def trajectory_summary(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if payload is None:
        return {
            "traj_exists": False,
            "messages": 0,
            "commands": 0,
            "api_calls": None,
            "cost": None,
            "route": "missing",
            "metrics": {},
            "notes": ["missing trajectory"],
        }
    messages = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(messages, list):
        return {
            "traj_exists": True,
            "messages": 0,
            "commands": 0,
            "api_calls": None,
            "cost": None,
            "route": "unknown",
            "metrics": {},
            "notes": ["unrecognized trajectory format"],
        }
    commands = extract_commands(messages)
    command_text = "\n".join(commands)
    metrics = {
        name: len(re.findall(pattern, command_text, flags=re.IGNORECASE | re.MULTILINE))
        for name, pattern in COMMAND_PATTERNS.items()
    }
    first_reference = next(
        (idx for idx, cmd in enumerate(commands) if re.search(COMMAND_PATTERNS["reference_runs"], cmd, re.IGNORECASE)),
        None,
    )
    first_edit = next(
        (idx for idx, cmd in enumerate(commands) if re.search(COMMAND_PATTERNS["implementation_edits"], cmd, re.IGNORECASE)),
        None,
    )
    notes: list[str] = []
    if metrics["reference_runs"] < 5:
        notes.append("limited reference probing")
    if metrics["invalid_error_probes"] == 0:
        notes.append("no obvious invalid/error probes")
    if metrics["stdin_probes"] == 0:
        notes.append("no obvious stdin probes")
    if metrics["dependency_attempts"] > 0:
        notes.append("tried unavailable external dependencies")
    if first_edit is not None and (first_reference is None or first_edit <= first_reference + 2):
        notes.append("implementation started very early")
    info = payload.get("info") or {}
    stats = info.get("model_stats") or {}
    return {
        "traj_exists": True,
        "messages": len(messages),
        "commands": len(commands),
        "api_calls": stats.get("api_calls"),
        "cost": stats.get("instance_cost"),
        "route": infer_route_sequence(commands, assistant_text(messages)),
        "metrics": metrics,
        "first_reference_command": first_reference,
        "first_implementation_command": first_edit,
        "notes": notes,
    }


def parse_exit_statuses(root: Path) -> dict[tuple[str, str], str]:
    statuses: dict[tuple[str, str], str] = {}
    for variant in DEFAULT_VARIANTS:
        for path in (root / variant).glob("exit_statuses_*.yaml"):
            current_status = None
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.rstrip()
                stripped = line.strip()
                if stripped.endswith(":") and not stripped.startswith("-") and stripped != "instances_by_exit_status:":
                    current_status = stripped[:-1]
                    continue
                if stripped.startswith("- ") and current_status:
                    statuses[(variant, stripped[2:])] = current_status
    return statuses


def compact_failures(categories: dict[str, int], limit: int = 3) -> str:
    if not categories:
        return ""
    return ", ".join(f"{name}:{count}" for name, count in Counter(categories).most_common(limit))


def compact_notes(notes: list[str]) -> str:
    return "; ".join(notes) if notes else "none obvious"


def explain_pair(task_id: str, baseline: dict[str, Any], scaffold: dict[str, Any]) -> dict[str, Any]:
    b_eval = baseline["eval"]
    s_eval = scaffold["eval"]
    b_traj = baseline["trajectory"]
    s_traj = scaffold["trajectory"]
    delta = None
    if b_eval["score"] is not None and s_eval["score"] is not None:
        delta = round(s_eval["score"] - b_eval["score"], 2)

    failure_reasons: list[str] = []
    b_metrics = b_traj.get("metrics") or {}
    if b_eval.get("error_code"):
        failure_reasons.append(f"eval error: {b_eval['error_code']}")
    if b_metrics.get("dependency_attempts", 0) > 0:
        failure_reasons.append("spent budget on external dependencies")
    if b_metrics.get("reference_runs", 0) < 5:
        failure_reasons.append("weak reference executable exploration")
    if b_metrics.get("invalid_error_probes", 0) == 0:
        failure_reasons.append("missed invalid/error behavior probing")
    if b_metrics.get("stdin_probes", 0) == 0:
        failure_reasons.append("missed stdin behavior probing")
    if not failure_reasons:
        failure_reasons.append("remaining failures concentrated in hidden behavior categories")

    effect_reasons: list[str] = []
    s_metrics = s_traj.get("metrics") or {}
    if delta is not None and delta > 0:
        effect_reasons.append(f"score improved by {delta} points")
    if b_traj.get("route") != s_traj.get("route"):
        effect_reasons.append(f"implementation route changed: {b_traj.get('route')} -> {s_traj.get('route')}")
    if b_metrics.get("dependency_attempts", 0) > s_metrics.get("dependency_attempts", 0):
        effect_reasons.append("reduced external dependency attempts")
    if s_metrics.get("reference_runs", 0) > b_metrics.get("reference_runs", 0):
        effect_reasons.append("more reference probing")
    b_score = b_eval.get("score")
    s_score = s_eval.get("score")
    if (
        b_score is not None
        and s_score is not None
        and s_metrics.get("compile_calls", 0) <= b_metrics.get("compile_calls", 0)
        and s_score > b_score
    ):
        effect_reasons.append("more direct compile/implementation path")
    if not effect_reasons:
        effect_reasons.append("effect unclear from current trajectory; inspect manually")

    return {
        "task": task_id,
        "baseline_score": b_eval["score"],
        "scaffold_score": s_eval["score"],
        "delta": delta,
        "baseline_failure_hypothesis": "; ".join(failure_reasons),
        "scaffold_effect_hypothesis": "; ".join(effect_reasons),
    }


def analyze_root(root: Path, tasks: list[str], tasks_root: Path, *, skip_missing: bool = False) -> dict[str, Any]:
    statuses = parse_exit_statuses(root)
    rows = []
    pair_rows = []
    by_task: dict[str, dict[str, Any]] = defaultdict(dict)
    for task_id in tasks:
        scope = load_task_scope(tasks_root, task_id)
        for variant in DEFAULT_VARIANTS:
            run_dir = root / variant / task_id
            eval_data = eval_summary(run_dir / f"{task_id}.eval.json", scope)
            traj_data = trajectory_summary(run_dir / f"{task_id}.traj.json")
            if skip_missing and not eval_data["eval_exists"] and not traj_data["traj_exists"]:
                continue
            status = statuses.get((variant, task_id))
            if status is None and (run_dir / "submission.tar.gz").exists():
                status = "Submitted?"
            elif status is None and traj_data["traj_exists"]:
                status = "unknown"
            elif status is None:
                status = "missing"
            row = {
                "root": str(root),
                "task": task_id,
                "variant": variant,
                "status": status,
                "eval": eval_data,
                "trajectory": traj_data,
            }
            rows.append(row)
            by_task[task_id][variant] = row
        if "baseline" in by_task[task_id] and "oracle_spec" in by_task[task_id]:
            pair_rows.append(explain_pair(task_id, by_task[task_id]["baseline"], by_task[task_id]["oracle_spec"]))
    return {"root": str(root), "rows": rows, "pairs": pair_rows}


def write_markdown(payload: dict[str, Any], out: Path) -> None:
    lines = ["# ProgramBench Trajectory Analysis", ""]
    for root_result in payload["roots"]:
        lines.extend(
            [
                f"## Run Root: `{root_result['root']}`",
                "",
                "### Variant Metrics",
                "",
                "| task | variant | status | score | pass/total | cost | calls | route | cmds | ref | err probes | stdin | deps | compiles | notes |",
                "|---|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in root_result["rows"]:
            ev = row["eval"]
            tr = row["trajectory"]
            metrics = tr.get("metrics") or {}
            pass_total = f"{ev['passed']}/{ev['total']}" if ev["eval_exists"] else "missing"
            cost = tr["cost"]
            cost_s = f"{cost:.2f}" if isinstance(cost, (int, float)) else ""
            lines.append(
                "| `{task}` | {variant} | {status} | {score} | {pass_total} | {cost} | {calls} | {route} | {cmds} | {ref} | {err} | {stdin} | {deps} | {compiles} | {notes} |".format(
                    task=row["task"],
                    variant=row["variant"],
                    status=row["status"],
                    score=ev["score"],
                    pass_total=pass_total,
                    cost=cost_s,
                    calls=tr["api_calls"] if tr["api_calls"] is not None else "",
                    route=tr["route"],
                    cmds=tr["commands"],
                    ref=metrics.get("reference_runs", 0),
                    err=metrics.get("invalid_error_probes", 0),
                    stdin=metrics.get("stdin_probes", 0),
                    deps=metrics.get("dependency_attempts", 0),
                    compiles=metrics.get("compile_calls", 0),
                    notes=compact_notes(tr.get("notes") or []),
                )
            )
        lines.extend(
            [
                "",
                "### Baseline vs Scaffold Interpretation",
                "",
                "| task | baseline | scaffold | delta | why baseline failed | why scaffold helped |",
                "|---|---:|---:|---:|---|---|",
            ]
        )
        for pair in root_result["pairs"]:
            lines.append(
                "| `{task}` | {baseline_score} | {scaffold_score} | {delta} | {fail} | {effect} |".format(
                    task=pair["task"],
                    baseline_score=pair["baseline_score"],
                    scaffold_score=pair["scaffold_score"],
                    delta=pair["delta"],
                    fail=pair["baseline_failure_hypothesis"],
                    effect=pair["scaffold_effect_hypothesis"],
                )
            )
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-roots",
        nargs="+",
        type=Path,
        default=[Path("reports/programbench_upper_bound_runs_arch_plan")],
    )
    parser.add_argument("--tasks", nargs="+", default=DEFAULT_TASKS)
    parser.add_argument(
        "--tasks-root",
        type=Path,
        default=Path("external/ProgramBench/src/programbench/data/tasks"),
    )
    parser.add_argument("--out-md", type=Path, default=Path("reports/programbench_trajectory_analysis.md"))
    parser.add_argument("--out-json", type=Path, default=Path("reports/programbench_trajectory_analysis.json"))
    parser.add_argument("--skip-missing", action="store_true")
    args = parser.parse_args()

    payload = {
        "tasks": args.tasks,
        "output_roots": [str(root) for root in args.output_roots],
        "roots": [
            analyze_root(root, args.tasks, args.tasks_root, skip_missing=args.skip_missing)
            for root in args.output_roots
        ],
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(payload, args.out_md)
    print(json.dumps({"markdown": str(args.out_md), "json": str(args.out_json)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
