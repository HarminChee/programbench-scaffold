#!/usr/bin/env python3
"""Build an artifact-backed summary for the ten-repository V2 Go cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


BASELINES: dict[str, dict[str, Any]] = {
    "sclevine__yj.8016400": {
        "native_line": 75.9, "native_statement": 76.2,
        "pb_functions": 651, "pb_line": 88.5, "pb_statement": 88.8,
    },
    "tomnomnom__gron.88a6234": {
        "pb_functions": 224, "pb_line": 92.9, "pb_statement": 93.3,
    },
    "multiprocessio__dsq.c3ae0ba": {
        "pb_functions": 507, "pb_line": 88.3, "pb_statement": 91.4,
    },
    "psampaz__go-mod-outdated.bb79367": {
        "pb_functions": 266, "pb_line": 100.0, "pb_statement": 100.0,
    },
    "rs__jplot.2a54bcc": {
        "pb_functions": 546, "pb_line": 77.9, "pb_statement": 81.1,
    },
    "mibk__dupl.1bf052b": {
        "pb_functions": 360, "pb_line": 93.8, "pb_statement": 94.6,
    },
    "astaxie__bat.17d1080": {
        "pb_functions": 946, "pb_line": None, "pb_statement": None,
        "pb_note": "official baseline unreliable due external HTTP dependencies",
    },
    "cheat__cheat.b8098dc": {
        "pb_functions": 289, "pb_line": 79.9, "pb_statement": 82.4,
        "pb_note": "official suite contains a known cleanroom mismatch",
    },
    "boyter__scc.515f91c": {
        "pb_functions": 464, "pb_line": 92.0, "pb_statement": 91.3,
        "pb_note": "official suite contains known tie/golden mismatches",
    },
    "alecthomas__chroma.8d04def": {
        "pb_functions": 192, "pb_line": 88.0, "pb_statement": 88.8,
    },
}


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def duplicates(counter: Counter[str]) -> int:
    return sum(count - 1 for count in counter.values() if count > 1)


def normalized_arg(value: Any) -> str:
    text = str(value)
    if text.startswith("-"):
        return text.split("=", 1)[0] + ("=<VALUE>" if "=" in text else "")
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return "<NUMBER>"
    if "/" in text or "\\" in text or "." in text:
        return "<PATH>"
    return "<VALUE>"


def normalize_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): normalize_tree(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [normalize_tree(item) for item in value]
    return value


def structural_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key).rsplit(".", 1)[-1] if "." in str(key) else str(key): structural_tree(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, list):
        return [structural_tree(item) for item in value]
    if value in (None, "", False):
        return value
    return f"<{type(value).__name__}>"


def diversity(manifest: Path) -> dict[str, Any]:
    cases = json.loads(manifest.read_text(encoding="utf-8")).get("cases") or []
    exact: Counter[str] = Counter()
    invocation: Counter[str] = Counter()
    structural: Counter[str] = Counter()
    behavior: Counter[str] = Counter()
    for case in cases:
        invocation_spec = {
            "argv0": case.get("argv0"),
            "args": case.get("args") or [],
            "env": case.get("env") or {},
            "stdin_sha256": case.get("stdin_sha256"),
            "stdin_regular_file": bool(case.get("stdin_regular_file")),
            "files": normalize_tree(case.get("files") or {}),
            "binary_files": normalize_tree(case.get("binary_files") or {}),
            "executable_files": normalize_tree(case.get("executable_files") or {}),
            "file_modes": normalize_tree(case.get("file_modes") or {}),
            "repeat_files": normalize_tree(case.get("repeat_files") or {}),
            "observe_files": case.get("observe_files") or [],
            "git": normalize_tree(case.get("git") or {}),
            "http": normalize_tree(case.get("http") or {}),
            "terminal": normalize_tree(case.get("terminal") or {}),
            "isolate_home_tmp": bool(case.get("isolate_home_tmp")),
            "timeout": case.get("timeout"),
        }
        behavior_spec = {
            "returncode": case.get("returncode"),
            "stdout_sha256": case.get("stdout_sha256"),
            "stderr_sha256": case.get("stderr_sha256"),
            "timed_out": bool(case.get("timed_out")),
            "observed_files": normalize_tree(case.get("observed_files") or {}),
            "stdout_mode": case.get("stdout_mode"),
        }
        structural_spec = {
            "args": [normalized_arg(arg) for arg in (case.get("args") or [])],
            "env_keys": sorted((case.get("env") or {}).keys()),
            "stdin": bool(case.get("stdin_sha256") and case.get("stdin_sha256") != hashlib.sha256(b"").hexdigest()),
            "files": structural_tree(case.get("files") or {}),
            "binary_files": structural_tree(case.get("binary_files") or {}),
            "executable_files": structural_tree(case.get("executable_files") or {}),
            "repeat_files": structural_tree(case.get("repeat_files") or {}),
            "observe_file_count": len(case.get("observe_files") or []),
            "git": bool(case.get("git")),
            "http": bool(case.get("http")),
            "terminal": bool(case.get("terminal")),
        }
        invocation[stable_hash(invocation_spec)] += 1
        behavior[stable_hash(behavior_spec)] += 1
        exact[stable_hash({"invocation": invocation_spec, "behavior": behavior_spec})] += 1
        structural[stable_hash(structural_spec)] += 1
    total = len(cases)

    def metric(counter: Counter[str]) -> dict[str, Any]:
        count = duplicates(counter)
        return {
            "duplicate_cases": count,
            "duplicate_percent": round(100.0 * count / total, 1) if total else 0.0,
            "unique_groups": len(counter),
        }

    return {
        "cases": total,
        "exact": metric(exact),
        "structural": metric(structural),
        "invocation": metric(invocation),
        "behavior": metric(behavior),
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def count_native_go_tests(work_dir: str | None) -> int | None:
    if not work_dir:
        return None
    source = Path(work_dir) / "source"
    if not source.is_dir():
        return None
    pattern = re.compile(r"(?m)^func\s+(Test[A-Za-z0-9_]*)\s*\(")
    return sum(
        len(pattern.findall(path.read_text(encoding="utf-8", errors="ignore")))
        for path in source.rglob("*_test.go")
        if path.is_file()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    instances = load_json(args.cohort)["instances"]
    rows: list[dict[str, Any]] = []
    for instance in instances:
        labels = ["yj_v2_expansive_union", "v2_expansive_final"]
        selected = next(
            (
                label
                for label in labels
                if (args.run_root / "generated" / instance / label / "oracle_tests" / "eval" / "generated_cli_manifest.json").is_file()
                and (args.run_root / "coverage" / instance / f"{label}.go_coverage_summary.json").is_file()
            ),
            None,
        )
        if selected is None:
            rows.append({"instance_id": instance, "status": "incomplete"})
            continue
        manifest = args.run_root / "generated" / instance / selected / "oracle_tests" / "eval" / "generated_cli_manifest.json"
        coverage_path = args.run_root / "coverage" / instance / f"{selected}.go_coverage_summary.json"
        quality_path = args.run_root / "generated" / instance / selected / "evaluation_quality_report.json"
        coverage = load_json(coverage_path)
        generated = coverage.get("generated_tests") or coverage.get("test_suite") or {}
        native = coverage.get("native_tests") or {}
        baseline = BASELINES.get(instance, {})
        native_line = native.get("line_coverage_percent")
        native_statement = native.get("statement_coverage_percent")
        if native_line is None:
            native_line = baseline.get("native_line")
        if native_statement is None:
            native_statement = baseline.get("native_statement")
        quality = load_json(quality_path) if quality_path.is_file() else {}
        repeat_check = quality.get("repeat_check") or {}
        repeat_summary = repeat_check.get("junit_summary") or {}
        repeat_passed = (
            repeat_check.get("pytest_returncode") == 0
            and not repeat_check.get("timed_out")
            and repeat_summary.get("failures", 0) == 0
            and repeat_summary.get("errors", 0) == 0
        )
        rows.append(
            {
                "instance_id": instance,
                "status": "passed"
                if generated.get("pytest_all_coverage_runs_passed")
                and coverage.get("all_branch_binary_comparisons_consistent")
                and quality.get("all_dummies_rejected")
                and (quality.get("assertion_lint") or {}).get("passed")
                and (quality.get("source_leak_scan") or {}).get("passed")
                and repeat_passed
                else "needs_repair",
                "suite_label": selected,
                "generated_line_coverage": generated.get("line_coverage_percent"),
                "generated_statement_coverage": generated.get("statement_coverage_percent"),
                "native_test_functions": count_native_go_tests(coverage.get("work_dir")),
                "native_line_coverage": native_line,
                "native_statement_coverage": native_statement,
                "pb_official_test_functions": baseline.get("pb_functions"),
                "pb_official_line_coverage": baseline.get("pb_line"),
                "pb_official_statement_coverage": baseline.get("pb_statement"),
                "pb_official_note": baseline.get("pb_note"),
                "three_binary_consistent": coverage.get("all_branch_binary_comparisons_consistent"),
                "quality": {
                    "all_dummies_rejected": quality.get("all_dummies_rejected"),
                    "dummy_passing_test_count": quality.get("dummy_passing_test_count"),
                    "assertion_lint_passed": (quality.get("assertion_lint") or {}).get("passed"),
                    "source_leak_passed": (quality.get("source_leak_scan") or {}).get("passed"),
                    "repeat_passed": repeat_passed if repeat_check else None,
                },
                "duplicates": diversity(manifest),
                "manifest": str(manifest),
                "coverage_summary": str(coverage_path),
                "quality_report": str(quality_path),
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
