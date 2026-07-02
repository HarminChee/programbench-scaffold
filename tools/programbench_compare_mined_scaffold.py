#!/usr/bin/env python3
"""Compare original baseline, test-only upper bound, and fair mined scaffold."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from programbench_trajectory_analyzer import compact_notes, trajectory_summary  # noqa: E402


DEFAULT_TASKS = [
    "sclevine__yj.8016400",
    "rs__jplot.2a54bcc",
    "multiprocessio__dsq.c3ae0ba",
]


def programbench_score(path: Path, instances: dict[str, Any], iid: str) -> dict[str, Any]:
    from programbench.eval.eval import EvaluationResult
    from programbench.utils.load_data import get_active_branches, get_ignored_tests

    inst = instances[iid]
    result = (
        EvaluationResult.model_validate_json(path.read_text(encoding="utf-8"))
        .for_branches(get_active_branches(inst))
        .without_ignored(get_ignored_tests(inst))
    )
    return {
        "passed": result.n_resolved,
        "total": len(result),
        "score": result.score * 100,
        "rounded": round(result.score * 100),
        "error_code": result.error_code,
        "warnings": len(result.warnings or []),
    }


def fmt_score(score: dict[str, Any]) -> str:
    return f"{score['passed']}/{score['total']} ({score['score']:.2f}%)"


def load_base_rows(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {row["task"]: row for row in payload["rows"]}


def note_for(row: dict[str, Any]) -> str:
    baseline = row["baseline"]["score"]["score"]
    test_only = row["test_only"]["score"]["score"]
    mined = row["mined"]["score"]["score"]
    if mined > test_only:
        return "mined scaffold exceeded this run's test-only upper-bound run; likely helped planning plus preserved prompt exploration"
    if mined > baseline and test_only > baseline:
        return "mined scaffold recovers part of the spec gap"
    if mined < baseline:
        return "negative pilot; current generic mined specs are not rich enough for this task"
    return "small or mixed effect"


def build(args: argparse.Namespace) -> dict[str, Any]:
    from programbench.utils.load_data import load_all_instances

    instances = {item["instance_id"]: item for item in load_all_instances(include_tests=True)}
    base_rows = load_base_rows(args.baseline_test_only_json)
    rows = []
    for iid in args.tasks:
        base = base_rows[iid]
        mined_dir = args.mined_root / iid
        mined_score = programbench_score(mined_dir / f"{iid}.eval.json", instances, iid)
        mined_traj = trajectory_summary(mined_dir / f"{iid}.traj.json")
        payload = json.loads((mined_dir / f"{iid}.traj.json").read_text(encoding="utf-8"))
        info = payload.get("info") or {}
        stats = info.get("model_stats") or {}
        mined_traj["status"] = info.get("exit_status")
        mined_traj["cost"] = stats.get("instance_cost")
        mined_traj["api_calls"] = stats.get("api_calls")
        row = {
            "task": iid,
            "lang": base["lang"],
            "baseline": base["baseline"],
            "test_only": base["test_only"],
            "mined": {"score": mined_score, "trajectory": mined_traj},
            "delta_vs_baseline": mined_score["score"] - base["baseline"]["score"]["score"],
            "delta_vs_test_only": mined_score["score"] - base["test_only"]["score"]["score"],
        }
        row["note"] = note_for(row)
        rows.append(row)
    return {
        "baseline_test_only_json": str(args.baseline_test_only_json),
        "mined_root": str(args.mined_root),
        "rows": rows,
        "aggregate": {
            "baseline_avg": sum(row["baseline"]["score"]["score"] for row in rows) / len(rows),
            "test_only_avg": sum(row["test_only"]["score"]["score"] for row in rows) / len(rows),
            "mined_avg": sum(row["mined"]["score"]["score"] for row in rows) / len(rows),
            "mined_improved_vs_baseline": sum(
                1 for row in rows if row["delta_vs_baseline"] > 0
            ),
            "mined_reached_or_exceeded_test_only": sum(
                1 for row in rows if row["delta_vs_test_only"] >= 0
            ),
        },
    }


def write_md(payload: dict[str, Any], out: Path) -> None:
    agg = payload["aggregate"]
    lines = [
        "# ProgramBench Fair Mined-Scaffold Pilot",
        "",
        "## Summary",
        "",
        f"- Tasks evaluated: {len(payload['rows'])}",
        f"- Original baseline average on these tasks: {agg['baseline_avg']:.2f}%",
        f"- Test-only upper-bound average on these tasks: {agg['test_only_avg']:.2f}%",
        f"- Fair mined-scaffold average on these tasks: {agg['mined_avg']:.2f}%",
        f"- Mined scaffold improved over baseline: {agg['mined_improved_vs_baseline']}/{len(payload['rows'])}",
        f"- Mined scaffold reached/exceeded test-only run: {agg['mined_reached_or_exceeded_test_only']}/{len(payload['rows'])}",
        "",
        "The mined scaffold is fair: it uses only bundled documentation plus black-box executions of the reference executable. It does not read official tests, test metadata, or source code.",
        "",
        "## Result Table",
        "",
        "| task | baseline | test-only | mined scaffold | mined vs baseline | mined vs test-only | status | note |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in payload["rows"]:
        lines.append(
            "| `{task}` | {base} | {test} | {mined} | {db:+.2f} | {dt:+.2f} | {status} | {note} |".format(
                task=row["task"],
                base=fmt_score(row["baseline"]["score"]),
                test=fmt_score(row["test_only"]["score"]),
                mined=fmt_score(row["mined"]["score"]),
                db=row["delta_vs_baseline"],
                dt=row["delta_vs_test_only"],
                status=row["mined"]["trajectory"].get("status"),
                note=row["note"],
            )
        )
    lines.extend(
        [
            "",
            "## Trajectory Signals",
            "",
            "| task | mined route | mined notes | cost | calls |",
            "|---|---|---|---:|---:|",
        ]
    )
    for row in payload["rows"]:
        traj = row["mined"]["trajectory"]
        cost = traj.get("cost")
        calls = traj.get("api_calls")
        lines.append(
            "| `{task}` | {route} | {notes} | {cost} | {calls} |".format(
                task=row["task"],
                route=traj.get("route"),
                notes=compact_notes(traj.get("notes") or []),
                cost=f"{cost:.3f}" if isinstance(cost, (int, float)) else "",
                calls=calls if calls is not None else "",
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `yj`: mined docs/probes exposed exact CLI and stdin conversion behavior; the agent improved strongly over baseline.",
            "- `jplot`: documentation summary plus option/error probes nearly matched the test-only score, suggesting docs+black-box process evidence can recover much of the spec gap for some CLI tools.",
            "- `dsq`: the current generic scaffold underperformed baseline. The mined spec captured invocation and basic file behavior, but not enough SQL/query and multi-format semantics. This is the next target for smarter probing and prioritization.",
            "",
            "Next fair-scaffold iteration should add task-aware probe synthesis from docs/help: generate concrete CSV/JSON/SQL examples for data-query tools, conversion matrices for format-conversion tools, and option-argument probes for flags that require values.",
            "",
        ]
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", nargs="+", default=DEFAULT_TASKS)
    parser.add_argument(
        "--baseline-test-only-json",
        type=Path,
        default=REPO_ROOT / "reports/programbench_baseline_vs_test_only_10task_2026-07-02.json",
    )
    parser.add_argument(
        "--mined-root",
        type=Path,
        default=REPO_ROOT / "reports/programbench_mined_scaffold_pilot_2026-07-02/mined_spec",
    )
    parser.add_argument(
        "--out-md",
        type=Path,
        default=REPO_ROOT / "reports/programbench_mined_scaffold_pilot_2026-07-02.md",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=REPO_ROOT / "reports/programbench_mined_scaffold_pilot_2026-07-02.json",
    )
    args = parser.parse_args()
    payload = build(args)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_md(payload, args.out_md)
    print(json.dumps({"markdown": str(args.out_md), "json": str(args.out_json)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
