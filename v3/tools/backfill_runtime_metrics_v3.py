#!/usr/bin/env python3
"""Recover coverage runtime metrics from existing V3 command logs.

New coverage runs write timing directly into their summary JSON. This helper
keeps older completed runs immutable and writes a sidecar per repository plus
one cohort report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def duration(payload: dict[str, Any]) -> float | None:
    value = payload.get("duration_seconds")
    if value is not None:
        return float(value)
    try:
        started = dt.datetime.fromisoformat(str(payload["started_at"]))
        ended = dt.datetime.fromisoformat(str(payload["ended_at"]))
    except (KeyError, TypeError, ValueError):
        return None
    return (ended - started).total_seconds()


def read_duration(path: Path) -> float | None:
    return duration(load(path)) if path.is_file() else None


def attempt_number(path: Path) -> int:
    match = re.search(r"coverage_(\d+)\.json$", path.name)
    return int(match.group(1)) if match else -1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for summary_path in sorted(args.run_root.glob("*/pipeline_summary.json")):
        repo_root = summary_path.parent
        pipeline = load(summary_path)
        coverage_path = Path(str(pipeline["coverage"]))
        coverage = load(coverage_path)
        coverage_pytest_seconds = 0.0
        coverage_pytest_runs = 0
        missing_logs: list[str] = []
        for branch in coverage.get("branch_results") or []:
            for result in branch.get("binary_results") or []:
                if result.get("label") != "coverage":
                    continue
                log_path = Path(str(result.get("log_path") or ""))
                seconds = read_duration(log_path)
                if seconds is None:
                    missing_logs.append(str(log_path))
                    continue
                coverage_pytest_seconds += seconds
                coverage_pytest_runs += 1

        logs_dir = Path(str(coverage["logs_dir"]))
        postprocess_paths = [
            logs_dir / "go_v3_final_covdata_percent.json",
            logs_dir / "go_v3_final_covdata_textfmt.json",
            logs_dir / "go_v3_final_cover_func.json",
        ]
        postprocess_values = [read_duration(path) for path in postprocess_paths]
        for path, seconds in zip(postprocess_paths, postprocess_values):
            if seconds is None:
                missing_logs.append(str(path))
        coverage_postprocess_seconds = sum(value or 0 for value in postprocess_values)

        attempts = []
        for path in sorted(
            (repo_root / "final_logs").glob("coverage_*.json"),
            key=attempt_number,
        ):
            payload = load(path)
            attempts.append(
                {
                    "attempt": attempt_number(path),
                    "returncode": payload.get("returncode"),
                    "timed_out": payload.get("timed_out"),
                    "total_harness_runtime_seconds": duration(payload),
                    "log": str(path),
                }
            )

        native_seconds = (
            ((coverage.get("native_tests") or {}).get("timing") or {}).get(
                "native_coverage_signal_seconds"
            )
        )
        row = {
            "instance_id": pipeline["instance_id"],
            "unit": "seconds",
            "coverage_pytest_seconds": round(coverage_pytest_seconds, 6),
            "coverage_postprocess_seconds": round(coverage_postprocess_seconds, 6),
            "statement_coverage_signal_seconds": round(
                coverage_pytest_seconds + coverage_postprocess_seconds, 6
            ),
            "coverage_pytest_runs": coverage_pytest_runs,
            "native_coverage_signal_seconds": native_seconds,
            "coverage_harness_attempts": attempts,
            "latest_total_harness_runtime_seconds": (
                attempts[-1]["total_harness_runtime_seconds"] if attempts else None
            ),
            "missing_logs": missing_logs,
            "definition": (
                "statement_coverage_signal_seconds is the coverage-instrumented pytest "
                "runtime plus covdata/textfmt/cover post-processing. Total harness time "
                "also includes clone, build, native tests, and binary consistency runs."
            ),
        }
        sidecar = repo_root / "runtime_metrics.json"
        sidecar.write_text(
            json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        row["sidecar"] = str(sidecar)
        rows.append(row)

    result = {
        "schema": "programbench_oracle_gym_v3_runtime_metrics",
        "unit": "seconds",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "repositories": len(rows),
                "complete_signal_timings": sum(not row["missing_logs"] for row in rows),
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
