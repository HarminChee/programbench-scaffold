#!/usr/bin/env python3
"""Collect comparable timed QEMU samples for every completed V3 repository."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DYNAMIC = HERE / "run_dynamic_path_metrics_v3.py"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cases", type=int, default=30)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for summary_path in sorted(args.run_root.glob("*/pipeline_summary.json")):
        pipeline = load(summary_path)
        instance = str(pipeline["instance_id"])
        coverage = load(Path(str(pipeline["coverage"])))
        executable = Path(str(coverage["work_dir"])) / "executable_source"
        output = summary_path.parent / f"dynamic_metrics_qemu{args.max_cases}_timed.json"
        if not (args.resume and output.is_file()):
            command = [
                sys.executable,
                str(DYNAMIC),
                "--manifest",
                str(pipeline["manifest"]),
                "--executable",
                str(executable),
                "--output",
                str(output),
                "--work-root",
                str(args.work_root / instance),
                "--max-cases",
                str(args.max_cases),
                "--sampling",
                "uniform",
                "--skip-callgrind",
                "--discard-raw-traces",
            ]
            completed = subprocess.run(command, text=True, capture_output=True)
            if completed.returncode != 0:
                rows.append(
                    {
                        "instance_id": instance,
                        "status": "failed",
                        "returncode": completed.returncode,
                        "stderr_tail": completed.stderr[-4000:],
                    }
                )
                continue
        dynamic = load(output)
        timing = dynamic.get("timing") or {}
        rows.append(
            {
                "instance_id": instance,
                "status": "completed",
                "requested_cases": dynamic.get("requested_cases"),
                "completed_cases": (dynamic.get("qemu") or {}).get("completed_cases"),
                "unsupported_cases": len(dynamic.get("unsupported_cases") or []),
                "qemu_runtime_seconds": timing.get("qemu_runtime_seconds"),
                "qemu_mean_case_seconds": timing.get("qemu_mean_case_seconds"),
                "total_collector_runtime_seconds": timing.get(
                    "total_collector_runtime_seconds"
                ),
                "output": str(output),
            }
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "schema": "programbench_oracle_gym_v3_qemu_timing_cohort",
                    "max_cases": args.max_cases,
                    "sampling": "uniform",
                    "rows": rows,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    result = {
        "schema": "programbench_oracle_gym_v3_qemu_timing_cohort",
        "max_cases": args.max_cases,
        "sampling": "uniform",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "completed": sum(row.get("status") == "completed" for row in rows),
                "total": len(rows),
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0 if all(row.get("status") == "completed" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
