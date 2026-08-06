#!/usr/bin/env python3
"""Finalize combined suites with bounded repository-level parallelism."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CROSS = ROOT / "v3" / "crosslang15" / "tools" / "run_crosslang_v3_final.py"
GO = ROOT / "v3" / "tools" / "run_go_v3_final.py"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--tasks-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--pytest-python", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    prep = {row["instance_id"]: row for row in json.loads((args.run_root / "preparation_summary.json").read_text())["rows"]}
    results: list[dict] = []
    pending: list[dict] = []
    for item in json.loads(args.cohort.read_text(encoding="utf-8"))["instances"]:
        instance = item["instance_id"]
        if prep.get(instance, {}).get("status") != "prepared":
            results.append({"instance_id": instance, "status": "not_prepared"})
            continue
        if (args.run_root / instance / "pipeline_summary.json").is_file():
            results.append({"instance_id": instance, "status": "skipped_completed"})
            continue
        pending.append(item)

    def finalize(item: dict) -> dict:
        instance = item["instance_id"]
        if item["language"] == "go":
            command = [sys.executable, str(GO), instance, "--run-root", str(args.run_root),
                       "--tasks-root", str(args.tasks_root), "--work-root", str(args.work_root),
                       "--xdist", "2", "--qemu-max-cases", "80"]
        else:
            command = [sys.executable, str(CROSS), instance, "--language", "rust" if item["language"] == "rs" else item["language"],
                       "--binary-name", item["binary_name"], "--run-root", str(args.run_root),
                       "--tasks-root", str(args.tasks_root), "--work-root", str(args.work_root),
                       "--pytest-python", str(args.pytest_python), "--xdist", "2", "--case-timeout", "5",
                       "--qemu-max-cases", "80", "--skip-pb-baseline"]
        result = subprocess.run(command, text=True, capture_output=True)
        evidence = {"instance_id": instance, "command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        log = args.run_root / "finalizer_logs" / f"{instance}.json"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps(evidence, indent=2) + "\n")
        return {"instance_id": instance, "status": "completed" if result.returncode == 0 else "failed", "returncode": result.returncode}

    workers = max(1, min(int(args.workers), 4, len(pending) or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(finalize, item): item["instance_id"] for item in pending}
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"instance_id": futures[future], "status": "failed", "error": str(exc)})
        (args.run_root / "finalizer_status.json").write_text(json.dumps({"results": results}, indent=2) + "\n")
    (args.run_root / "finalizer_status.json").write_text(json.dumps({"state": "completed", "results": results}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
