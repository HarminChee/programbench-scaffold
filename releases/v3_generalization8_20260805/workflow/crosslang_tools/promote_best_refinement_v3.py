#!/usr/bin/env python3
"""Promote only non-regressing, quality-accepted refinement suites.

The promoted root contains relative symlinks to the selected per-repository
artifacts.  This keeps multi-round runs cheap while allowing each repository to
inherit its own best-known suite instead of blindly replacing it with the most
recent round.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def coverage_pair(summary: dict[str, Any]) -> tuple[float, float] | None:
    stop = summary.get("stop_gate") or {}
    primary = stop.get("primary_percent")
    secondary = stop.get("secondary_percent")
    if primary is None or secondary is None:
        source = summary.get("source_coverage") or {}
        if "lines" in source:
            primary = (source.get("lines") or {}).get("percent")
            secondary = (source.get("regions") or {}).get("percent")
        else:
            primary = source.get("line_percent")
            secondary = source.get("branch_percent")
    if primary is None or secondary is None:
        return None
    return float(primary), float(secondary)


def quality_accepted(summary: dict[str, Any]) -> bool:
    stop = summary.get("stop_gate") or {}
    checks = stop.get("checks") or {}
    tests_passed = summary.get("all_tests_passed")
    if tests_passed is None:
        tests_passed = checks.get("generated_tests_passed")
    consistent = summary.get("binary_consistent")
    if consistent is None:
        consistent = checks.get("three_binary_consistent")
    coverage_valid = checks.get("coverage_signal_valid")
    if coverage_valid is None:
        coverage_valid = (summary.get("source_coverage") or {}).get("valid", True)
    return all(
        bool(value)
        for value in (
            tests_passed,
            consistent,
            checks.get("all_dummies_rejected"),
            checks.get("assertion_lint"),
            checks.get("source_leak"),
            coverage_valid,
            summary.get("case_count") or summary.get("manifest"),
        )
    )


def choose(prior: Path, candidate: Path, tolerance: float) -> tuple[Path, str]:
    prior_summary_path = prior / "pipeline_summary.json"
    candidate_summary_path = candidate / "pipeline_summary.json"
    if not candidate_summary_path.is_file():
        return prior, "candidate_missing"
    if not prior_summary_path.is_file():
        return candidate, "prior_missing"
    prior_summary, candidate_summary = load(prior_summary_path), load(candidate_summary_path)
    prior_ok, candidate_ok = quality_accepted(prior_summary), quality_accepted(candidate_summary)
    if prior_ok and not candidate_ok:
        return prior, "candidate_quality_rejected"
    if candidate_ok and not prior_ok:
        return candidate, "candidate_repairs_prior_quality"
    if not candidate_ok and not prior_ok:
        return prior, "both_quality_rejected_keep_prior"
    prior_cov, candidate_cov = coverage_pair(prior_summary), coverage_pair(candidate_summary)
    if candidate_cov is None:
        return prior, "candidate_coverage_invalid"
    if prior_cov is None:
        return candidate, "candidate_repairs_prior_coverage"
    non_regressing = all(new + tolerance >= old for new, old in zip(candidate_cov, prior_cov))
    if non_regressing:
        return candidate, "candidate_non_regressing"
    return prior, "candidate_coverage_regression"


def replace_symlink(link: Path, target: Path) -> None:
    if link.is_symlink() or link.exists():
        if link.is_dir() and not link.is_symlink():
            raise RuntimeError(f"refusing to replace real directory: {link}")
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(Path(os.path.relpath(target, link.parent)), target_is_directory=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prior-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--tolerance", type=float, default=0.05)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    rows = []
    instance_names = sorted(
        {path.name for path in args.prior_root.iterdir() if path.is_dir()}
        | {path.name for path in args.candidate_root.iterdir() if path.is_dir()}
    )
    for instance in instance_names:
        prior, candidate = args.prior_root / instance, args.candidate_root / instance
        if not prior.is_dir():
            selected, reason = candidate, "new_candidate_instance"
        elif not candidate.is_dir():
            selected, reason = prior, "instance_not_refined"
        else:
            selected, reason = choose(prior, candidate, args.tolerance)
        replace_symlink(args.output_root / instance, selected.resolve())
        rows.append(
            {
                "instance_id": instance,
                "selected": "candidate" if selected == candidate else "prior",
                "reason": reason,
                "selected_path": str(selected.resolve()),
            }
        )
    payload = {
        "schema": "programbench_v3_monotonic_refinement_promotion_v1",
        "policy": "candidate must pass quality gates and not regress either source coverage metric",
        "coverage_tolerance_pp": args.tolerance,
        "rows": rows,
    }
    args.output_summary.parent.mkdir(parents=True, exist_ok=True)
    args.output_summary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"instances": len(rows), "candidate_promoted": sum(r["selected"] == "candidate" for r in rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
