#!/usr/bin/env python3
"""Build and validate isolated V2 plans for the fixed ten-repo Go cohort."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-map", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--expansive", action="store_true", help="create four-perspective high-volume topic batches")
    args = parser.parse_args()
    source_map = json.loads(args.source_map.read_text(encoding="utf-8"))
    output_root = args.output_root.resolve()
    results = []
    for instance_id, source_text in source_map.items():
        source_dir = Path(source_text)
        plan_dir = output_root / instance_id / "plan"
        build = subprocess.run(
            [sys.executable, str(HERE / "build_behavior_plan_v2.py"), instance_id, "--source-dir", str(source_dir), "--output-dir", str(plan_dir)],
            capture_output=True,
            text=True,
        )
        validate = subprocess.run(
            [sys.executable, str(HERE / "validate_behavior_plan_v2.py"), "--plan-dir", str(plan_dir)],
            capture_output=True,
            text=True,
        ) if build.returncode == 0 else None
        batches = subprocess.run(
            [sys.executable, str(HERE / "build_topic_batches_v2.py"), "--plan-dir", str(plan_dir), "--output-dir", str(output_root / instance_id / "topic_batches"), "--grouped"] + (["--expansive"] if args.expansive else []),
            capture_output=True,
            text=True,
        ) if validate and validate.returncode == 0 else None
        results.append({
            "instance_id": instance_id,
            "source_dir": str(source_dir),
            "build_returncode": build.returncode,
            "validate_returncode": validate.returncode if validate else None,
            "batch_returncode": batches.returncode if batches else None,
            "build_stderr": build.stderr[-2000:],
            "validate_stderr": validate.stderr[-2000:] if validate else None,
            "batch_stderr": batches.stderr[-2000:] if batches else None,
        })
    payload = {"schema": "programbench_oracle_gym_v2_go10_planning", "results": results}
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "planning_summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"planned": sum(r["batch_returncode"] == 0 for r in results), "total": len(results), "summary": str(output_root / "planning_summary.json")}, indent=2))
    return 0 if all(r["batch_returncode"] == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
