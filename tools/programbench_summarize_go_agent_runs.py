#!/usr/bin/env python3
"""Summarize ProgramBench Go agent-oracle runs into JSON, CSV, and Markdown."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
from pathlib import Path
from typing import Any

from programbench_go_coverage_harness import parse_cover_profile_line_coverage


DEFAULT_WORKSPACE_ROOT = Path("/home/programbench/research/oracle-workspace")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def nested(data: dict[str, Any], *keys: str) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def first_existing(paths: list[Path]) -> Path | None:
    return next((path for path in paths if path.exists()), None)


def summarize_instance(workspace_root: Path, instance_id: str) -> dict[str, Any]:
    run_root = workspace_root / "experiments" / "runs" / instance_id
    final_path = run_root / "final" / "run_summary.json"
    final = read_json(final_path)
    pipeline_path_text = final.get("final_pipeline_summary")
    pipeline_candidates = []
    if pipeline_path_text:
        pipeline_candidates.append(Path(str(pipeline_path_text)))
    pipeline_candidates.extend(sorted((run_root / "iterations" / instance_id).glob("*/pipeline_summary.json"), reverse=True))
    pipeline_path = first_existing(pipeline_candidates)
    pipeline = read_json(pipeline_path) if pipeline_path else {}

    suite_label = str(pipeline.get("suite_label") or "agent_loop_iter_01")
    coverage_candidates = []
    if pipeline.get("coverage_summary_path"):
        coverage_candidates.append(Path(str(pipeline["coverage_summary_path"])))
    coverage_candidates.extend(sorted((run_root / "artifacts" / "coverage" / instance_id).glob("*.json"), reverse=True))
    coverage_path = first_existing(coverage_candidates)
    coverage = read_json(coverage_path) if coverage_path else {}

    capture_candidates = sorted(
        (run_root / "artifacts" / "generated" / instance_id).glob("*/quality_report.json"), reverse=True
    )
    capture_path = first_existing(capture_candidates)
    capture = read_json(capture_path) if capture_path else {}
    generated = coverage.get("generated_tests") or {}
    native = coverage.get("native_tests") or {}
    generated_line = generated.get("line_coverage_percent")
    native_line = native.get("line_coverage_percent")
    generated_backfilled = False
    native_backfilled = False
    if generated_line is None and generated.get("profile"):
        backfill = parse_cover_profile_line_coverage(Path(str(generated["profile"])))
        generated_line = backfill.get("line_coverage_percent")
        generated_backfilled = generated_line is not None
    if native_line is None and native.get("profile"):
        backfill = parse_cover_profile_line_coverage(Path(str(native["profile"])))
        native_line = backfill.get("line_coverage_percent")
        native_backfilled = native_line is not None
    quality = pipeline.get("quality") or {}
    history = final.get("history") or []
    latest_history = history[-1] if history else {}
    failed_step = pipeline.get("failed_step") or {}
    return {
        "instance_id": instance_id,
        "status": final.get("status") or "missing",
        "accepted": bool(final.get("accepted")),
        "repository": final.get("repository") or coverage.get("repository"),
        "commit": final.get("commit") or coverage.get("commit"),
        "suite_label": suite_label,
        "candidate_case_count": capture.get("candidate_case_count"),
        "stable_case_count": capture.get("case_count") or final.get("final_case_count"),
        "skipped_case_count": capture.get("skipped_case_count"),
        "generated_line_coverage_percent": generated_line,
        "generated_line_coverage_backfilled": generated_backfilled,
        "generated_statement_coverage_percent": generated.get(
            "total_statement_coverage_percent", generated.get("statement_coverage_percent")
        ),
        "native_line_coverage_percent": native_line,
        "native_line_coverage_backfilled": native_backfilled,
        "native_statement_coverage_percent": native.get(
            "total_statement_coverage_percent", native.get("statement_coverage_percent")
        ),
        "coverage_gap_count": latest_history.get("coverage_gap_count"),
        "generated_pytests_passed": generated.get("pytest_all_coverage_runs_passed"),
        "binary_behavior_consistent": coverage.get("all_branch_binary_comparisons_consistent"),
        "all_dummies_rejected": quality.get("all_dummies_rejected"),
        "source_leak_passed": quality.get("source_leak_passed"),
        "assertion_lint_passed": quality.get("assertion_lint_passed"),
        "quality_passed": latest_history.get("quality_passed"),
        "go_toolchain": nested(coverage, "go_toolchain", "selected_version"),
        "go_build_target": nested(coverage, "go_build_package", "selected"),
        "go_executable": nested(coverage, "go_build_package", "selected_go_executable"),
        "failed_step": failed_step.get("name"),
        "failed_returncode": failed_step.get("returncode"),
        "pipeline_summary": str(pipeline_path) if pipeline_path else None,
        "coverage_summary": str(coverage_path) if coverage_path else None,
        "capture_summary": str(capture_path) if capture_path else None,
    }


def coverage_stats(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 3) if values else None,
        "median": round(statistics.median(values), 3) if values else None,
        "minimum": round(min(values), 3) if values else None,
        "maximum": round(max(values), 3) if values else None,
    }


def markdown_report(summary: dict[str, Any]) -> str:
    counts = summary["status_counts"]
    stats = summary["coverage_statistics"]
    lines = [
        "# ProgramBench Go oracle 实验汇总",
        "",
        f"生成时间：{summary['generated_at']}",
        "",
        f"清单共 {summary['task_count']} 个 Go 仓库，已生成最终状态 {summary['completed_count']} 个。",
        "",
        "## 状态",
        "",
        "| 状态 | 数量 |",
        "|---|---:|",
    ]
    lines.extend(f"| `{status}` | {count} |" for status, count in sorted(counts.items()))
    lines.extend(
        [
            "",
            "## 覆盖率概览",
            "",
            "| 指标 | 有效仓库 | 平均值 | 中位数 | 最小值 | 最大值 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    labels = {
        "generated_line_coverage_percent": "生成测试行覆盖率",
        "generated_statement_coverage_percent": "生成测试语句覆盖率",
        "native_line_coverage_percent": "原生测试行覆盖率",
        "native_statement_coverage_percent": "原生测试语句覆盖率",
    }
    for key, label in labels.items():
        item = stats[key]
        values = [item[name] if item[name] is not None else "—" for name in ["mean", "median", "minimum", "maximum"]]
        lines.append(f"| {label} | {item['count']} | {values[0]} | {values[1]} | {values[2]} | {values[3]} |")
    lines.extend(
        [
            "",
            "## 仓库明细",
            "",
            "| 实例 | 状态 | 稳定用例 | 行覆盖率 | 语句覆盖率 | 生成测试通过 | 二进制一致 | 失败步骤 |",
            "|---|---|---:|---:|---:|---|---|---|",
        ]
    )
    for row in summary["instances"]:
        line_cov = row.get("generated_line_coverage_percent")
        stmt_cov = row.get("generated_statement_coverage_percent")
        lines.append(
            "| {instance_id} | `{status}` | {cases} | {line} | {statement} | {tests} | {binary} | {failed} |".format(
                instance_id=row["instance_id"],
                status=row["status"],
                cases=row.get("stable_case_count") if row.get("stable_case_count") is not None else "—",
                line=f"{line_cov:.1f}%" if isinstance(line_cov, (int, float)) else "—",
                statement=f"{stmt_cov:.1f}%" if isinstance(stmt_cov, (int, float)) else "—",
                tests=row.get("generated_pytests_passed") if row.get("generated_pytests_passed") is not None else "—",
                binary=row.get("binary_behavior_consistent") if row.get("binary_behavior_consistent") is not None else "—",
                failed=row.get("failed_step") or "—",
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE_ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    workspace_root = args.workspace_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    manifest = read_json(workspace_root / "manifests" / "programbench_go_all.json")
    instances = list(manifest.get("instances") or [])
    if not instances:
        instances = sorted(path.name for path in (workspace_root / "experiments" / "runs").iterdir() if path.is_dir())
    rows = [summarize_instance(workspace_root, instance_id) for instance_id in instances]
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        counts[status] = counts.get(status, 0) + 1
    coverage_keys = [
        "generated_line_coverage_percent",
        "generated_statement_coverage_percent",
        "native_line_coverage_percent",
        "native_statement_coverage_percent",
    ]
    summary = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "workspace_root": str(workspace_root),
        "task_count": len(rows),
        "completed_count": sum(1 for row in rows if row["status"] != "missing"),
        "status_counts": counts,
        "coverage_statistics": {key: coverage_stats(rows, key) for key in coverage_keys},
        "instances": rows,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "programbench_go_agent_runs.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (output_dir / "programbench_go_agent_runs.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["instance_id"])
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "programbench_go_agent_runs.md").write_text(markdown_report(summary), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "status_counts": counts}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
