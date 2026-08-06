#!/usr/bin/env python3
"""Build the artifact-backed V3 pilot report for yj, gron, and dsq."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


HELD_OUT_PB = {
    "sclevine__yj.8016400": {"test_functions": 651, "line": 88.5, "statement": 88.8},
    "tomnomnom__gron.88a6234": {"test_functions": 224, "line": 92.9, "statement": 93.3},
    "multiprocessio__dsq.c3ae0ba": {"test_functions": 507, "line": 88.3, "statement": 91.4},
}


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def duplicate_summary(manifest: Path) -> dict:
    cases = load(manifest).get("cases") or []
    invocations, behaviors, exact = Counter(), Counter(), Counter()
    for case in cases:
        invocation_value = {
            "args": case.get("args"), "env": case.get("env"), "stdin": case.get("stdin_sha256"),
            "files": case.get("files"), "git": case.get("git"), "http": case.get("http"),
            "terminal": case.get("terminal"),
        }
        behavior_value = {
            "returncode": case.get("returncode"), "stdout": case.get("stdout_sha256"),
            "stderr": case.get("stderr_sha256"), "timed_out": case.get("timed_out"),
            "observed_files": case.get("observed_files"),
        }
        invocations[digest(invocation_value)] += 1
        behaviors[digest(behavior_value)] += 1
        exact[digest({"invocation": invocation_value, "behavior": behavior_value})] += 1
    def metric(counter: Counter) -> dict:
        duplicate = sum(count - 1 for count in counter.values() if count > 1)
        return {
            "duplicate_cases": duplicate,
            "duplicate_percent": round(100 * duplicate / len(cases), 1) if cases else 0.0,
            "unique_groups": len(counter),
        }
    return {
        "cases": len(cases),
        "exact_execution": metric(exact),
        "invocation": metric(invocations),
        "behavior": metric(behaviors),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    rows = []
    for instance, pb in HELD_OUT_PB.items():
        repo = args.run_root / instance
        summary_path = repo / "pipeline_summary.json"
        if not summary_path.is_file():
            rows.append({"instance_id": instance, "status": "incomplete"})
            continue
        summary = load(summary_path)
        coverage = load(Path(summary["coverage"]))
        quality = load(Path(summary["quality"]))
        manifest = Path(summary["manifest"])
        sourcebuilt_dynamic = repo / "dynamic_metrics_sourcebuilt.json"
        dynamic_path = (
            sourcebuilt_dynamic
            if sourcebuilt_dynamic.is_file()
            else repo / "dynamic_metrics.json"
        )
        dynamic = load(dynamic_path) if dynamic_path.is_file() else None
        generated = coverage.get("generated_tests") or {}
        native = coverage.get("native_tests") or {}
        rows.append({
            "instance_id": instance,
            "status": summary.get("stop_gate", {}).get("status"),
            "v3_test_functions": duplicate_summary(manifest)["cases"],
            "v3_line_coverage": generated.get("line_coverage_percent"),
            "v3_statement_coverage": generated.get("statement_coverage_percent"),
            "native_line_coverage": native.get("line_coverage_percent"),
            "native_statement_coverage": native.get("statement_coverage_percent"),
            "pb_held_out": pb,
            "three_binary_consistent": coverage.get("all_branch_binary_comparisons_consistent"),
            "coverage_timing": coverage.get("timing"),
            "all_dummies_rejected": quality.get("all_dummies_rejected"),
            "duplicates": duplicate_summary(manifest),
            "dynamic_metrics": None if dynamic is None else {
                "binary_kind": (
                    "source-built-with-symbols"
                    if dynamic_path == sourcebuilt_dynamic
                    else "cleanroom"
                ),
                "qemu_cases": dynamic.get("qemu", {}).get("completed_cases"),
                "qemu_unique_blocks": dynamic.get("qemu", {}).get("unique_translation_blocks_union"),
                "qemu_unique_edges": dynamic.get("qemu", {}).get("unique_block_edges_union"),
                "qemu_first_party_blocks": dynamic.get("qemu", {}).get("unique_first_party_blocks_union"),
                "qemu_first_party_edges": dynamic.get("qemu", {}).get("unique_first_party_edges_union"),
                "qemu_first_party_afl_like_bitmap_slots": dynamic.get("qemu", {}).get(
                    "first_party_afl_like_bitmap_slots_union"
                ),
                "qemu_first_party_novelty": dynamic.get("qemu", {}).get("first_party_edge_novelty"),
                "afl_like_bitmap_slots": dynamic.get("qemu", {}).get("afl_like_bitmap_slots_union"),
                "callgrind_cases": dynamic.get("callgrind", {}).get("completed_cases"),
                "callgrind_functions": dynamic.get("callgrind", {}).get("unique_functions_union"),
                "tool_status": dynamic.get("tools"),
                "timing": dynamic.get("timing"),
            },
            "artifacts": {
                "summary": str(summary_path), "manifest": str(manifest),
                "coverage": summary["coverage"], "quality": summary["quality"],
                "dynamic": str(dynamic_path) if dynamic_path.is_file() else None,
            },
        })
    result = {
        "schema": "programbench_oracle_gym_v3_go3_pilot_report",
        "pb_baseline_role": "held-out comparison only; never generation input or stopping criterion",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
