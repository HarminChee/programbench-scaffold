#!/usr/bin/env python3
"""Create an auditable Native/V3/PB/QEMU table for the 20-repository cohort."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}


def native_declarations(source: Path, language: str) -> int:
    suffixes = {"go": {".go"}, "rs": {".rs"}, "c": {".c", ".h"}, "cpp": {".cc", ".cpp", ".cxx", ".h", ".hpp"}}[language]
    total = 0
    for path in source.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes or any(part in {"vendor", "target", "third_party", ".git"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if language == "go":
            total += len(re.findall(r"(?m)^\s*func\s+Test[A-Za-z0-9_]*\s*\(", text))
        elif language == "rs":
            total += len(re.findall(r"#\s*\[\s*(?:[A-Za-z0-9_:]+::)?test(?:\s*\([^]]*\))?\s*]\s*(?:pub\s+)?(?:async\s+)?fn\s+", text))
        else:
            total += len(re.findall(r"\b(?:TEST|TEST_F|TEST_P|TYPED_TEST|TEST_CASE)\s*\(", text))
    return total


def pb_functions(tasks_root: Path, instance: str) -> int:
    payload = load(tasks_root / instance / "tests.json")
    names = set()
    for branch in (payload.get("branches") or {}).values():
        if branch.get("ignored"):
            continue
        names.update(str(name) for name in branch.get("tests") or [])
    return len(names)


def pair(payload: dict, language: str) -> tuple[float | None, float | None]:
    if language == "go":
        return payload.get("line_coverage_percent"), payload.get("statement_coverage_percent")
    if language == "rs":
        return (payload.get("lines") or {}).get("percent"), (payload.get("regions") or {}).get("percent")
    return payload.get("line_percent"), payload.get("branch_percent")


def baseline(root: Path, instance: str, language: str) -> tuple[dict, dict, dict, dict]:
    directory = root / instance
    if language == "go":
        paths = list(directory.rglob("*.go_coverage_summary.json"))
        data = load(paths[0]) if paths else {}
        return data.get("native_tests") or {}, data.get("test_suite") or {}, data.get("timing") or {}, data
    data = load(directory / "native_and_pb_coverage.json")
    return data.get("native") or {}, data.get("coverage") or {}, data.get("timing") or {}, data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--tasks-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for item in load(args.cohort)["instances"]:
        instance, language = item["instance_id"], item["language"]
        pipeline = load(args.run_root / instance / "pipeline_summary.json")
        manifest = load(args.run_root / "generated" / instance / "v3_final" / "oracle_tests" / "eval" / "generated_cli_manifest.json")
        native, pb, baseline_timing, baseline_raw = baseline(args.baseline_root, instance, language)
        if language == "go":
            ours = pipeline.get("generated_tests") or {}
        else:
            ours = pipeline.get("source_coverage") or {}
        qemu = pipeline.get("dynamic_path_metrics") or load(args.run_root / instance / "coverage" / "v3_final.dynamic_path_metrics.json")
        rows.append({
            **item,
            "count_definition": {
                "native": "static native test declarations; language-specific macros/attributes/functions, not parameterized runtime executions",
                "v3": "retained behavioral cases; one generated pytest function per case",
                "pb": "unique active PB pytest node IDs across all active branches",
            },
            "native_test_declarations": native_declarations(args.source_root / instance, language),
            "v3_behavioral_test_functions": len(manifest.get("cases") or []),
            "pb_oracle_behavioral_test_functions": pb_functions(args.tasks_root, instance),
            "native_coverage_primary_secondary": pair(native, language),
            "v3_coverage_primary_secondary": pair(ours, language),
            "pb_coverage_primary_secondary": pair(pb, language),
            "coverage_labels": "line/statement" if language == "go" else "line/region" if language == "rs" else "line/branch",
            "v3_all_tests_passed": pipeline.get("all_tests_passed") if language != "go" else ((ours or {}).get("pytest_all_coverage_runs_passed")),
            "pb_all_tests_passed": baseline_raw.get("all_filtered_tests_passed") if language != "go" else ((pb or {}).get("pytest_all_coverage_runs_passed")),
            "native_coverage_seconds": baseline_timing.get("native_tests_and_coverage_seconds") or baseline_timing.get("native_coverage_signal_seconds"),
            "pb_coverage_seconds": baseline_timing.get("generated_suite_coverage_seconds") or baseline_timing.get("statement_coverage_signal_seconds"),
            "v3_coverage_seconds": (pipeline.get("coverage_timing") or {}).get("generated_suite_coverage_seconds") or (pipeline.get("coverage_timing") or {}).get("statement_coverage_signal_seconds"),
            "qemu": qemu,
            "quality_path": pipeline.get("quality"),
            "manifest_path": pipeline.get("manifest"),
        })
    result = {"schema": "programbench_oracle_gym_v3_crosslang20_results_v1", "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"repos": len(rows), "complete_v3": sum(row["v3_behavioral_test_functions"] > 0 for row in rows), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
