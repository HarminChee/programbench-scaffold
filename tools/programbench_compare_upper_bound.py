#!/usr/bin/env python3
"""Summarize baseline vs oracle-spec ProgramBench runs and trajectory failures."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_TASKS = [
    "sclevine__yj.8016400",
    "multiprocessio__dsq.c3ae0ba",
    "rs__jplot.2a54bcc",
]

CATEGORY_PATTERNS = {
    "flags/help/usage": r"flag|option|argparse|usage|help|version|invalid",
    "stdin/file/io": r"stdin|file|path|directory|input|read|write",
    "stdout/stderr/exit": r"stdout|stderr|exit|returncode|return code|error message",
    "format/conversion/query": r"json|yaml|toml|hcl|format|convert|selector|query|sql|csv",
    "edge/order/unicode": r"edge|empty|null|unicode|escape|nested|large|order|preserve|special",
    "runtime/build": r"compile|build|timeout|panic|exception|not_run|system_error",
}

TRAJECTORY_PATTERNS = {
    "reference_executable_runs": r"\./executable|/workspace/executable",
    "help_or_docs_checks": r"README|--help|\s-h\b|cat .*README|sed .*README",
    "invalid_or_error_probes": r"invalid|bad flag|unknown|error case|does-not-exist",
    "stdin_probes": r"stdin|printf|echo .*\\\||cat <<|here-doc",
    "file_probes": r"touch |mkdir |\.json|\.yaml|\.toml|\.csv|input\.",
    "implementation_edits": r"cat <<'EOF'|cat <<EOF|sed -i|go build|cargo build|compile\.sh|\.go\b|\.rs\b|\.py\b",
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
        f"{branch}/{test['name']}"
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


def score_eval(payload: dict[str, Any] | None, scope: dict[str, Any]) -> dict[str, Any]:
    if payload is None:
        return {"exists": False, "score": None, "n_resolved": 0, "n_tests": 0, "error_code": "missing_eval_json"}
    results = scoped_results(payload, scope)
    passed = sum(1 for item in results if item.get("status") == "passed")
    total = len(results)
    return {
        "exists": True,
        "score": round((passed / total) * 100, 2) if total else 0.0,
        "n_resolved": passed,
        "n_tests": total,
        "error_code": payload.get("error_code"),
        "error_details": payload.get("error_details"),
        "warnings": payload.get("warnings") or [],
    }


def categorize_failures(payload: dict[str, Any] | None, scope: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    if payload is None:
        counts["missing_eval_json"] += 1
        return counts
    if payload.get("error_code"):
        counts["runtime/build"] += 1
    for item in scoped_results(payload, scope):
        status = item.get("status")
        if status in {"passed", "skipped"}:
            continue
        text = " ".join(
            [
                str(item.get("name", "")),
                str(status or ""),
                str((item.get("extra") or {}).get("message", "")),
                str((item.get("extra") or {}).get("text", "")),
            ]
        ).lower()
        matched = False
        for category, pattern in CATEGORY_PATTERNS.items():
            if re.search(pattern, text):
                counts[category] += 1
                matched = True
        if not matched:
            counts["other"] += 1
    return counts


def flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(flatten_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(flatten_text(item) for item in value.values())
    return ""


def analyze_trajectory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "notes": ["missing trajectory"]}
    payload = json.loads(path.read_text(encoding="utf-8"))
    messages = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(messages, list):
        return {"exists": True, "notes": ["unrecognized trajectory format"]}
    text = flatten_text(messages)
    counts = {name: len(re.findall(pattern, text, flags=re.IGNORECASE)) for name, pattern in TRAJECTORY_PATTERNS.items()}
    notes = []
    if counts["reference_executable_runs"] < 8:
        notes.append("limited reference executable probing")
    if counts["invalid_or_error_probes"] == 0:
        notes.append("no obvious invalid/error probes")
    if counts["stdin_probes"] == 0:
        notes.append("no obvious stdin probes")
    if counts["implementation_edits"] > 0 and counts["reference_executable_runs"] < counts["implementation_edits"]:
        notes.append("may have started implementing before enough probing")
    return {
        "exists": True,
        "message_count": len(messages),
        "probe_counts": counts,
        "notes": notes,
    }


def compact_counter(counter: Counter[str], limit: int = 4) -> str:
    if not counter:
        return ""
    return ", ".join(f"{name}:{count}" for name, count in counter.most_common(limit))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=Path("reports/programbench_upper_bound_runs"))
    parser.add_argument(
        "--tasks-root",
        type=Path,
        default=Path("external/ProgramBench/src/programbench/data/tasks"),
    )
    parser.add_argument("--tasks", nargs="+", default=DEFAULT_TASKS)
    parser.add_argument("--out-md", type=Path)
    parser.add_argument("--out-json", type=Path)
    args = parser.parse_args()

    rows = []
    for task_id in args.tasks:
        scope = load_task_scope(args.tasks_root, task_id)
        variant_data = {}
        for variant in ("baseline", "oracle_spec"):
            run_dir = args.output_root / variant / task_id
            eval_payload = load_json(run_dir / f"{task_id}.eval.json")
            traj_path = run_dir / f"{task_id}.traj.json"
            variant_data[variant] = {
                "eval": score_eval(eval_payload, scope),
                "failure_categories": dict(categorize_failures(eval_payload, scope)),
                "trajectory": analyze_trajectory(traj_path),
            }
        baseline_score = variant_data["baseline"]["eval"]["score"]
        oracle_score = variant_data["oracle_spec"]["eval"]["score"]
        delta = None
        if baseline_score is not None and oracle_score is not None:
            delta = round(oracle_score - baseline_score, 4)
        rows.append(
            {
                "task": task_id,
                "baseline": variant_data["baseline"],
                "oracle_spec": variant_data["oracle_spec"],
                "delta": delta,
            }
        )

    payload = {"output_root": str(args.output_root), "tasks": args.tasks, "rows": rows}
    out_json = args.out_json or args.output_root / "upper_bound_comparison.json"
    out_md = args.out_md or args.output_root / "upper_bound_comparison.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# ProgramBench Upper-Bound Comparison",
        "",
        "| task | baseline score | oracle-spec score | delta | baseline failures | oracle failures | trajectory notes |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for row in rows:
        base = row["baseline"]
        oracle = row["oracle_spec"]
        notes = "; ".join(base["trajectory"].get("notes") or [])
        lines.append(
            "| `{task}` | {bs} | {os} | {delta} | {bf} | {of} | {notes} |".format(
                task=row["task"],
                bs=base["eval"]["score"],
                os=oracle["eval"]["score"],
                delta=row["delta"],
                bf=compact_counter(Counter(base["failure_categories"])),
                of=compact_counter(Counter(oracle["failure_categories"])),
                notes=notes,
            )
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(out_json), "markdown": str(out_md)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
