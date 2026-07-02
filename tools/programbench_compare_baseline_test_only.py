#!/usr/bin/env python3
"""Compare original ProgramBench baseline against greenfield test-only runs."""

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


TASKS = [
    ("sirwart__ripsecrets.34c9e03", "rs", "ripsecrets_test_only"),
    ("wfxr__csview.8ac4de0", "rs", "csview_test_only"),
    ("wfxr__code-minimap.0ddeea5", "rs", "code_minimap_test_only"),
    ("clog-tool__clog-cli.7066cba", "rs", "clog_cli_test_only"),
    ("drew-alleman__datasurgeon.d257cee", "rs", "datasurgeon_test_only"),
    ("sclevine__yj.8016400", "go", "yj_test_only"),
    ("multiprocessio__dsq.c3ae0ba", "go", "dsq_test_only"),
    ("rs__jplot.2a54bcc", "go", "jplot_test_only"),
    ("cmatsuoka__figlet.202a0a8", "c", "figlet_test_only"),
    ("cslarsen__jp2a.61d205f", "c", "jp2a_test_only"),
]


def score(path: Path, instances: dict[str, Any], iid: str) -> dict[str, Any]:
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


def traj(path: Path) -> dict[str, Any]:
    summary = trajectory_summary(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    info = payload.get("info") or {}
    stats = info.get("model_stats") or {}
    summary["status"] = info.get("exit_status")
    summary["cost"] = stats.get("instance_cost")
    summary["api_calls"] = stats.get("api_calls")
    return summary


def failure_note(row: dict[str, Any]) -> str:
    notes = []
    base = row["baseline"]
    if base["score"]["error_code"]:
        notes.append(f"baseline eval error: {base['score']['error_code']}")
    bmetrics = base["trajectory"].get("metrics") or {}
    if bmetrics.get("dependency_attempts", 0) > 0:
        notes.append("baseline tried unavailable dependencies")
    if base["score"]["score"] == 0 and not notes:
        notes.append("baseline produced no passing tests")
    delta = row["delta"]
    if delta > 10:
        notes.append("test-only specs gave strong positive signal")
    elif delta > 0:
        notes.append("test-only specs gave modest improvement")
    elif delta < 0:
        notes.append("test-only underperformed baseline; likely raw tests distracted or plan changed")
    else:
        notes.append("no measurable score change")
    return "; ".join(notes)


def build(args: argparse.Namespace) -> dict[str, Any]:
    from programbench.utils.load_data import load_all_instances

    instances = {item["instance_id"]: item for item in load_all_instances(include_tests=True)}
    rows = []
    for iid, lang, test_folder in TASKS:
        base_dir = args.baseline_root / iid
        test_dir = args.test_only_root / test_folder / iid
        baseline_score = score(base_dir / f"{iid}.eval.json", instances, iid)
        test_score = score(test_dir / f"{iid}.eval.json", instances, iid)
        row = {
            "task": iid,
            "lang": lang,
            "baseline": {
                "score": baseline_score,
                "trajectory": traj(base_dir / f"{iid}.traj.json"),
            },
            "test_only": {
                "score": test_score,
                "trajectory": traj(test_dir / f"{iid}.traj.json"),
            },
            "delta": test_score["score"] - baseline_score["score"],
        }
        row["note"] = failure_note(row)
        rows.append(row)
    return {
        "baseline_root": str(args.baseline_root),
        "test_only_root": str(args.test_only_root),
        "rows": rows,
        "aggregate": {
            "baseline_avg": sum(row["baseline"]["score"]["score"] for row in rows) / len(rows),
            "test_only_avg": sum(row["test_only"]["score"]["score"] for row in rows) / len(rows),
            "improved": sum(1 for row in rows if row["delta"] > 0),
            "regressed": sum(1 for row in rows if row["delta"] < 0),
            "baseline_compile_failed": sum(
                1 for row in rows if row["baseline"]["score"]["error_code"] == "compile_failed"
            ),
        },
    }


def fmt_score(score: dict[str, Any]) -> str:
    return f"{score['passed']}/{score['total']} ({score['score']:.2f}%)"


def write_md(payload: dict[str, Any], out: Path) -> None:
    agg = payload["aggregate"]
    lines = [
        "# ProgramBench Original Baseline vs Test-Only Scaffold",
        "",
        "## Summary",
        "",
        f"- Original baseline average: {agg['baseline_avg']:.2f}%",
        f"- Test-only scaffold average: {agg['test_only_avg']:.2f}%",
        f"- Average delta: {agg['test_only_avg'] - agg['baseline_avg']:+.2f} points",
        f"- Improved tasks: {agg['improved']}/10",
        f"- Regressed tasks: {agg['regressed']}/10",
        f"- Baseline compile failures: {agg['baseline_compile_failed']}/10",
        "",
        "## Result Table",
        "",
        "| task | lang | baseline | test-only | delta | baseline status | test-only status | note |",
        "|---|---:|---:|---:|---:|---|---|---|",
    ]
    for row in payload["rows"]:
        lines.append(
            "| `{task}` | {lang} | {base} | {test} | {delta:+.2f} | {bs} | {ts} | {note} |".format(
                task=row["task"],
                lang=row["lang"],
                base=fmt_score(row["baseline"]["score"]),
                test=fmt_score(row["test_only"]["score"]),
                delta=row["delta"],
                bs=row["baseline"]["trajectory"].get("status"),
                ts=row["test_only"]["trajectory"].get("status"),
                note=row["note"],
            )
        )
    lines.extend(
        [
            "",
            "## Trajectory Signals",
            "",
            "| task | baseline route | baseline notes | test-only route | test-only notes |",
            "|---|---|---|---|---|",
        ]
    )
    for row in payload["rows"]:
        btraj = row["baseline"]["trajectory"]
        ttraj = row["test_only"]["trajectory"]
        lines.append(
            "| `{task}` | {br} | {bn} | {tr} | {tn} |".format(
                task=row["task"],
                br=btraj.get("route"),
                bn=compact_notes(btraj.get("notes") or []),
                tr=ttraj.get("route"),
                tn=compact_notes(ttraj.get("notes") or []),
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The original baseline is substantially weaker than the test-only upper-bound setting on this dev set. The most important signal is not just score improvement: three baseline runs compile-failed, and several successful baseline submissions stayed far below test-only scores. This supports Robin's hypothesis that missing or poorly exposed specs are a major bottleneck.",
            "",
            "The two regressions (`code-minimap`, `jp2a`) are also useful: raw executable tests can distract the agent or push it toward a worse implementation plan. A fair scaffold should therefore summarize and prioritize mined behavior instead of dumping raw observations.",
            "",
        ]
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline-root",
        type=Path,
        default=REPO_ROOT / "reports/programbench_original_baseline_10task_2026-07-02/baseline",
    )
    parser.add_argument(
        "--test-only-root",
        type=Path,
        default=REPO_ROOT / "reports/programbench_greenfield_test_runs",
    )
    parser.add_argument(
        "--out-md",
        type=Path,
        default=REPO_ROOT / "reports/programbench_baseline_vs_test_only_10task_2026-07-02.md",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=REPO_ROOT / "reports/programbench_baseline_vs_test_only_10task_2026-07-02.json",
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
