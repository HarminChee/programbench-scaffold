#!/usr/bin/env python3
"""Run the native/PB held-out baseline collector with bounded concurrency."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_baseline.py")


def successful(output_root: Path, instance: str) -> bool:
    directory = output_root / instance
    if (directory / "native_and_pb_coverage.json").is_file() or list(directory.rglob("*.go_coverage_summary.json")):
        return True
    path = output_root / instance / "run.json"
    if not path.is_file():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8"))["returncode"] == 0
    except (OSError, KeyError, json.JSONDecodeError):
        return False


def run(row: dict, args: argparse.Namespace) -> dict:
    command = [
        sys.executable, str(SCRIPT), row["instance_id"], "--language", row["language"],
        "--binary-name", row["binary_name"], "--work-root", str(args.work_root),
        "--output-root", str(args.output_root),
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    return {"instance_id": row["instance_id"], "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    rows = json.loads(args.cohort.read_text(encoding="utf-8"))["instances"]
    pending = [row for row in rows if not successful(args.output_root, row["instance_id"])]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run, row, args): row for row in pending}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            status = {"completed_this_run": results, "remaining": len(pending) - len(results)}
            (args.output_root / "coordinator_status.json").write_text(json.dumps(status, indent=2) + "\n")
    complete = sum(successful(args.output_root, row["instance_id"]) for row in rows)
    (args.output_root / "coordinator_status.json").write_text(json.dumps({
        "state": "completed", "successful": complete, "total": len(rows), "results": results,
    }, indent=2) + "\n")
    print(json.dumps({"successful": complete, "total": len(rows)}))
    return 0 if complete == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
