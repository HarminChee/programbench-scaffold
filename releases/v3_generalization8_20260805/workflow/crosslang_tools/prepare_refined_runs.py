#!/usr/bin/env python3
"""Combine initial and refinement candidates in a clean versioned run root."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
COMBINE = ROOT / "v3" / "tools" / "combine_candidates_v3.py"


def valid_candidates(repo: Path) -> list[Path]:
    result = []
    candidate_root = repo / "candidates"
    if not candidate_root.is_dir():
        candidate_root = repo / "aggregate_candidates"
    for path in sorted(candidate_root.glob("batch_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if payload.get("status") != "rejected_batch" and payload.get("cases"):
            result.append(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--initial-root", type=Path, required=True)
    parser.add_argument("--refinement-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for item in json.loads(args.cohort.read_text(encoding="utf-8"))["instances"]:
        instance = item["instance_id"]
        initial, refinement, output = args.initial_root / instance, args.refinement_root / instance, args.output_root / instance
        initial_paths, refinement_paths = valid_candidates(initial), valid_candidates(refinement)
        expected_refinement = 0
        manifest = refinement / "batches" / "batch_manifest.json"
        if manifest.is_file():
            expected_refinement = len(json.loads(manifest.read_text(encoding="utf-8"))["batches"])
        if not initial_paths or len(refinement_paths) != expected_refinement or not expected_refinement:
            rows.append({"instance_id": instance, "status": "incomplete", "initial": len(initial_paths), "refinement": len(refinement_paths), "expected_refinement": expected_refinement})
            continue
        if (output / "plan").exists():
            shutil.rmtree(output / "plan")
        shutil.copytree(refinement / "plan", output / "plan")
        prior_signals = initial / "case_signals.json"
        if prior_signals.is_file():
            shutil.copy2(prior_signals, output / "case_signals.json")
        aggregate = output / "aggregate_candidates"
        if aggregate.exists():
            shutil.rmtree(aggregate)
        aggregate.mkdir(parents=True)
        for index, path in enumerate(initial_paths, 1):
            shutil.copy2(path, aggregate / f"batch_initial_{index:03d}.json")
        for index, path in enumerate(refinement_paths, 1):
            shutil.copy2(path, aggregate / f"batch_refinement_{index:03d}.json")
        merged = output / "merged" / "v3_candidates.json"
        subprocess.run([sys.executable, str(COMBINE), "--candidate-dir", str(aggregate), "--output", str(merged)], check=True)
        rows.append({"instance_id": instance, "status": "prepared", "initial_batches": len(initial_paths), "refinement_batches": len(refinement_paths)})
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "preparation_summary.json").write_text(json.dumps({"rows": rows}, indent=2) + "\n")
    print(json.dumps({"prepared": sum(row["status"] == "prepared" for row in rows), "total": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
