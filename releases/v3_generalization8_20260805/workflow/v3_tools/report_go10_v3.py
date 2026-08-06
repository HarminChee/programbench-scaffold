#!/usr/bin/env python3
"""Build an artifact-backed V3/V2/native/PB comparison for the Go10 cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def duplicates(manifest: Path) -> dict:
    cases = load(manifest).get("cases") or []
    exact: Counter[str] = Counter()
    invocation: Counter[str] = Counter()
    behavior: Counter[str] = Counter()
    for case in cases:
        inv = {
            key: case.get(key)
            for key in (
                "args", "env", "stdin_sha256", "files", "binary_files",
                "executable_files", "repeat_files", "file_modes", "git",
                "http", "terminal", "isolate_home_tmp", "stdin_regular_file",
                "timeout", "timeout_seconds", "observe_files",
            )
        }
        beh = {
            key: case.get(key)
            for key in (
                "returncode", "stdout_sha256", "stderr_sha256", "timed_out",
                "observed_files",
            )
        }
        inv_key, beh_key = digest(inv), digest(beh)
        invocation[inv_key] += 1
        behavior[beh_key] += 1
        exact[digest((inv_key, beh_key))] += 1

    def metric(counter: Counter[str]) -> dict:
        duplicate = sum(count - 1 for count in counter.values() if count > 1)
        return {
            "duplicate_cases": duplicate,
            "duplicate_percent": round(100 * duplicate / len(cases), 1) if cases else 0,
            "unique_groups": len(counter),
        }

    return {
        "cases": len(cases),
        "exact_execution": metric(exact),
        "invocation": metric(invocation),
        "behavior": metric(behavior),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, required=True)
    ap.add_argument("--cohort", type=Path, required=True)
    ap.add_argument("--v2-baseline", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    instances = load(args.cohort)["instances"]
    v2 = {row["instance_id"]: row for row in load(args.v2_baseline)}
    rows = []
    for instance in instances:
        summary_path = args.run_root / instance / "pipeline_summary.json"
        if not summary_path.is_file():
            rows.append({"instance_id": instance, "status": "incomplete"})
            continue
        summary = load(summary_path)
        coverage = load(Path(summary["coverage"]))
        manifest = Path(summary["manifest"])
        generated = coverage.get("generated_tests") or {}
        native = coverage.get("native_tests") or {}
        baseline = v2.get(instance) or {}
        filter_path = args.run_root / instance / "low_value_filter_report.json"
        if not filter_path.is_file():
            filter_path = args.run_root / instance / "quality_filter_report.json"
        rows.append({
            "instance_id": instance,
            "status": summary.get("stop_gate", {}).get("status"),
            "v3_tests": duplicates(manifest)["cases"],
            "v3_line": generated.get("line_coverage_percent"),
            "v3_statement": generated.get("statement_coverage_percent"),
            "native_tests": baseline.get("native_test_functions"),
            "native_line": native.get("line_coverage_percent"),
            "native_statement": native.get("statement_coverage_percent"),
            "pb_tests": baseline.get("pb_official_test_functions"),
            "pb_line": baseline.get("pb_official_line_coverage"),
            "pb_statement": baseline.get("pb_official_statement_coverage"),
            "pb_note": baseline.get("pb_official_note"),
            "v2_tests": (baseline.get("duplicates") or {}).get("cases"),
            "v2_line": baseline.get("generated_line_coverage"),
            "v2_statement": baseline.get("generated_statement_coverage"),
            "three_binary_consistent": coverage.get(
                "all_branch_binary_comparisons_consistent"
            ),
            "coverage_timing": coverage.get("timing"),
            "duplicates": duplicates(manifest),
            "quality_filter": load(filter_path) if filter_path.is_file() else None,
            "artifacts": {
                "summary": str(summary_path),
                "manifest": str(manifest),
                "coverage": summary["coverage"],
                "quality": summary["quality"],
            },
        })
    result = {
        "schema": "programbench_oracle_gym_v3_go10_quality_report",
        "pb_role": "held-out baseline only; never generation input",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({
        "complete": sum(row.get("status") != "incomplete" for row in rows),
        "total": len(rows),
        "output": str(args.output),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
