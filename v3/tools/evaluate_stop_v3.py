#!/usr/bin/env python3
"""Evaluate PB-independent V3 quality and absolute coverage stopping gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", type=Path, required=True)
    ap.add_argument("--quality", type=Path, required=True)
    ap.add_argument("--profiles", type=Path, required=True)
    ap.add_argument("--language", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    coverage = json.loads(args.coverage.read_text())
    quality = json.loads(args.quality.read_text())
    profile = json.loads(args.profiles.read_text())[args.language]
    generated = coverage.get("generated_tests") or {}
    primary = generated.get("statement_coverage_percent") if args.language == "go" else generated.get("primary_coverage_percent")
    secondary = generated.get("line_coverage_percent") if args.language == "go" else generated.get("secondary_coverage_percent")
    checks = {
        "minimum_primary": primary is not None and primary >= profile["minimum_primary_percent"],
        "stretch_primary": primary is not None and primary >= profile["stretch_primary_percent"],
        "minimum_secondary": secondary is not None and secondary >= profile["minimum_secondary_percent"],
        "stretch_secondary": secondary is not None and secondary >= profile.get("stretch_secondary_percent", profile["minimum_secondary_percent"]),
        "three_binary_consistent": bool(coverage.get("all_branch_binary_comparisons_consistent")),
        "generated_tests_passed": bool(generated.get("pytest_all_coverage_runs_passed")),
        "all_dummies_rejected": bool(quality.get("all_dummies_rejected")),
        "assertion_lint": bool((quality.get("assertion_lint") or {}).get("passed")),
        "source_leak": bool((quality.get("source_leak_scan") or {}).get("passed")),
    }
    accepted_minimum = all(value for key, value in checks.items() if key not in {"stretch_primary", "stretch_secondary"})
    accepted_stretch = accepted_minimum and checks["stretch_primary"] and checks["stretch_secondary"]
    result = {
        "schema": "programbench_oracle_gym_v3_stop_gate",
        "pb_baseline_used": False,
        "primary_percent": primary,
        "secondary_percent": secondary,
        "checks": checks,
        "status": "accepted_stretch" if accepted_stretch else "accepted_minimum" if accepted_minimum else "refine",
        "note": "saturation rounds and reachable-coverage audit are required before a final production stop",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
