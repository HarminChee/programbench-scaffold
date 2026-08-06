#!/usr/bin/env python3
"""Prepare a PB-oracle-free second generation round from our own coverage."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
V3 = ROOT / "v3" / "tools"


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def primary_coverage(summary: dict, language: str) -> float | None:
    if language == "go":
        return ((summary.get("generated_tests") or {}).get("line_coverage_percent"))
    coverage = summary.get("source_coverage") or {}
    if language == "rs":
        return ((coverage.get("lines") or {}).get("percent"))
    return coverage.get("line_percent")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--initial-root", type=Path, required=True)
    parser.add_argument("--refinement-root", type=Path, required=True)
    args = parser.parse_args()
    cohort = json.loads(args.cohort.read_text(encoding="utf-8"))
    rows = []
    for item in cohort["instances"]:
        instance = item["instance_id"]
        initial = args.initial_root / instance
        summary_path = initial / "pipeline_summary.json"
        if not summary_path.is_file():
            rows.append({"instance_id": instance, "status": "initial_missing"})
            continue
        target = args.refinement_root / instance
        if (target / "plan").exists():
            shutil.rmtree(target / "plan")
        if (target / "batches").exists():
            shutil.rmtree(target / "batches")
        shutil.copytree(initial / "plan", target / "plan")
        feedback_json = target / "plan" / "refinement_feedback.json"
        feedback_md = target / "plan" / "refinement_feedback.md"
        run([
            sys.executable, str(V3 / "build_refinement_feedback_v3.py"), instance,
            "--prior-run-root", str(args.initial_root), "--output-json", str(feedback_json),
            "--output-md", str(feedback_md),
        ])
        run([
            sys.executable, str(V3 / "build_batches_v3.py"),
            "--plan-dir", str(target / "plan"), "--output-dir", str(target / "batches"),
            "--rows-per-batch", "9999", "--minimum-cases", "45",
            "--perspectives", "reachability_and_anchor_repair,source_corpus_harvest,source_and_entrypoints",
        ])
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        coverage = primary_coverage(summary, item["language"])
        threshold = 85.0
        model = "claude-opus-4.8"
        rows.append({
            "instance_id": instance, "status": "prepared", "initial_primary_coverage": coverage,
            "model": model,
            "reason": f"All refinement rounds use Opus; the generic continuation threshold is {threshold}%",
        })
    args.refinement_root.mkdir(parents=True, exist_ok=True)
    (args.refinement_root / "preparation_summary.json").write_text(json.dumps({"rows": rows}, indent=2) + "\n")
    print(json.dumps({"prepared": sum(row["status"] == "prepared" for row in rows), "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
