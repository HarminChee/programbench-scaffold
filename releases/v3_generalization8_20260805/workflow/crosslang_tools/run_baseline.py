#!/usr/bin/env python3
"""Collect native and held-out PB source coverage for one cohort instance."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import datetime as dt
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools"
TASKS = Path("/home/programbench/research/programbench/src/programbench/data/tasks")
PYTEST = Path("/home/programbench/research/programbench-scaffold/.venv/bin/python")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_id")
    parser.add_argument("--language", choices=("c", "cpp", "rs", "go"), required=True)
    parser.add_argument("--binary-name", required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_root / args.instance_id
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.language == "go":
        command = [
            str(PYTEST), str(TOOLS / "programbench_go_coverage_harness.py"), args.instance_id,
            "--tasks-root", str(TASKS), "--branch", "all",
            "--work-root", str(args.work_root / args.instance_id),
            "--output-root", str(output_dir), "--run-native-tests",
            "--xdist", "2", "--pytest-timeout", "2400", "--overwrite",
        ]
    else:
        command = [
            str(PYTEST), str(TOOLS / "programbench_native_coverage_harness.py"), args.instance_id,
            "--tasks-root", str(TASKS), "--branch", "all",
            "--work-root", str(args.work_root / args.instance_id),
            "--output-json", str(output_dir / "native_and_pb_coverage.json"),
            "--pytest-python", str(PYTEST), "--xdist", "2", "--coverage-xdist", "1",
            "--pytest-timeout", "2400", "--fixed-workspace", "/workspace",
            "--binary-name", args.binary_name, "--coverage-only", "--overwrite",
        ]
    started = time.perf_counter()
    env = os.environ.copy()
    env["PATH"] = "/usr/local/go1.21.13/bin:" + env.get("PATH", "")
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    if args.language == "go":
        artifacts = list(output_dir.rglob("*.go_coverage_summary.json"))
    else:
        artifacts = [output_dir / "native_and_pb_coverage.json"] if (output_dir / "native_and_pb_coverage.json").is_file() else []
    usable = bool(artifacts)
    evidence = {
        "schema": "programbench_v3_crosslang20_baseline_run_v1",
        "instance_id": args.instance_id, "language": args.language,
        "command": command, "returncode": 0 if usable else result.returncode,
        "collector_returncode": result.returncode, "artifact_usable": usable,
        "artifacts": [str(path) for path in artifacts],
        "duration_seconds": round(time.perf_counter() - started, 6),
        "stdout": result.stdout, "stderr": result.stderr,
    }
    latest = output_dir / "run.json"
    if latest.is_file():
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        latest.replace(output_dir / f"run_attempt_{stamp}.json")
    latest.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"instance_id": args.instance_id, "returncode": 0 if usable else result.returncode, "collector_returncode": result.returncode, "output": str(output_dir)}))
    return 0 if usable else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
