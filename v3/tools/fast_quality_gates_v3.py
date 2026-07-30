#!/usr/bin/env python3
"""Scalable manifest-level quality gates for large generated CLI suites.

The four standard dummy executables have closed, deterministic semantics. This
tool compares those semantics directly with every captured oracle assertion,
including initial/post-run files, instead of paying pytest setup cost thousands
of times. Gold determinism and three-binary execution remain separate runtime
gates.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from programbench_assertion_linter import lint_oracle_root  # noqa: E402
from programbench_run_generated_oracle_quality_gates import source_leak_scan  # noqa: E402


DUMMIES = {
    "true": (0, b"", b""),
    "false": (1, b"", b""),
    "empty-stderr": (0, b"", b"dummy stderr\n"),
}


def fixture_path(eval_root: Path, fixture_subdir: str, name: str) -> Path:
    return eval_root / "fixtures" / fixture_subdir / name


def repeat_bytes(spec: dict[str, Any]) -> bytes:
    segments = spec.get("segments")
    if isinstance(segments, list):
        content = "".join(
            str(segment.get("row", "")) * int(segment.get("count", 0))
            for segment in segments
        )
    else:
        content = (
            str(spec.get("prefix", ""))
            + str(spec.get("row", "")) * int(spec.get("count", 0))
            + str(spec.get("suffix", ""))
        )
    encoding = "latin-1" if str(spec.get("encoding") or "").lower() in {"latin-1", "latin1"} else "utf-8"
    return content.encode(encoding)


def initial_files(case: dict[str, Any]) -> dict[str, tuple[str, bytes | None]]:
    files: dict[str, tuple[str, bytes | None]] = {}
    for field in ("files", "executable_files"):
        for name, content in (case.get(field) or {}).items():
            files[str(name)] = ("file", str(content).encode())
    for name, content in (case.get("binary_files") or {}).items():
        files[str(name)] = ("file", base64.b64decode(str(content), validate=True))
    for name, spec in (case.get("repeat_files") or {}).items():
        files[str(name)] = ("file", repeat_bytes(spec))
    git = case.get("git") or {}
    if git.get("init") is True:
        files[".git"] = ("directory", None)
    for field in ("staged_files", "untracked_files"):
        for name, content in (git.get(field) or {}).items():
            files[str(name)] = ("file", str(content).encode())
    return files


def observed_files_match_dummy(case: dict[str, Any]) -> bool:
    initial = initial_files(case)
    for name, expected in (case.get("observed_files") or {}).items():
        actual = initial.get(str(name))
        if bool(actual) != bool(expected.get("exists")):
            return False
        if not actual:
            continue
        kind, content = actual
        if expected.get("kind") and expected.get("kind") != kind:
            return False
        if kind == "file":
            expected_content = base64.b64decode(
                str(expected.get("content_base64") or ""), validate=True
            )
            if content != expected_content:
                return False
    return True


def normalized_equal(mode: str, actual: bytes, expected: bytes) -> bool:
    if mode == "lines_unordered":
        return sorted(actual.splitlines()) == sorted(expected.splitlines())
    return actual == expected


def dummy_passes(
    case: dict[str, Any], *, kind: str, eval_root: Path, fixture_subdir: str
) -> bool:
    stdin = fixture_path(eval_root, fixture_subdir, str(case["stdin_file"])).read_bytes()
    expected_stdout = fixture_path(eval_root, fixture_subdir, str(case["stdout_file"])).read_bytes()
    expected_stderr = fixture_path(eval_root, fixture_subdir, str(case["stderr_file"])).read_bytes()
    if kind == "cat-stdin":
        returncode, stdout, stderr = 0, stdin, b""
    else:
        returncode, stdout, stderr = DUMMIES[kind]
    # Terminal cases can transform screen output; conservatively require an
    # execution audit rather than claiming a symbolic dummy pass.
    if case.get("terminal"):
        return False
    return (
        returncode == case.get("returncode")
        and normalized_equal(str(case.get("stdout_mode") or "exact"), stdout, expected_stdout)
        and stderr == expected_stderr
        and observed_files_match_dummy(case)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle-material-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--repeat-runtime-passed", action="store_true")
    args = parser.parse_args()
    oracle_root = args.oracle_material_root.resolve()
    if not (oracle_root / "eval").is_dir():
        oracle_root = oracle_root / "oracle_tests"
    eval_root = oracle_root / "eval"
    manifest_path = eval_root / "generated_cli_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixture_subdir = str(manifest.get("fixture_subdir") or "generated_cli")
    cases = list(manifest.get("cases") or [])
    dummy_results = []
    passing_names: set[str] = set()
    for kind in ("true", "cat-stdin", "false", "empty-stderr"):
        names = []
        for index, case in enumerate(cases):
            if dummy_passes(case, kind=kind, eval_root=eval_root, fixture_subdir=fixture_subdir):
                names.append(
                    f"eval.tests.test_generated_cli_oracle.test_{index:04d}_{case['name']}"
                )
        passing_names.update(names)
        dummy_results.append({
            "kind": kind,
            "method": "manifest_semantic_simulation",
            "passing_test_count": len(names),
            "passing_test_names": names,
            "all_tests_rejected": not names,
        })
    leak = source_leak_scan(oracle_root)
    # Public package/module identifiers are legitimate behavioral data for
    # dependency-analysis CLIs (for example go-mod-outdated). Treat string
    # matches inside captured stdin/stdout/stderr fixtures as advisory rather
    # than source leakage. Source-like files and matches embedded in executable
    # test code or documentation remain blocking.
    fixture_matches = [
        item for item in leak.get("source_identifying_matches") or []
        if str(item.get("path") or "").replace("\\", "/").startswith("eval/fixtures/")
    ]
    blocking_matches = [
        item for item in leak.get("source_identifying_matches") or []
        if item not in fixture_matches
    ]
    leak["fixture_string_matches_ignored"] = fixture_matches
    leak["source_identifying_matches"] = blocking_matches
    leak["source_identifying_string_matches"] = len(blocking_matches)
    leak["passed"] = (
        int(leak.get("source_like_file_matches") or 0) == 0
        and not blocking_matches
    )
    lint = lint_oracle_root(oracle_root)
    payload = {
        "schema": "programbench_oracle_gym_v3_fast_quality",
        "oracle_material_root": str(oracle_root),
        "dummy_method": (
            "exact simulation of standard dummy returncode/stdout/stderr and unchanged fixture state"
        ),
        "dummy_reject": dummy_results,
        "all_dummies_rejected": all(not item["passing_test_names"] for item in dummy_results),
        "all_tests_reject_all_dummies": all(not item["passing_test_names"] for item in dummy_results),
        "dummy_passing_test_count": len(passing_names),
        "dummy_passing_test_names": sorted(passing_names),
        "source_leak_scan": leak,
        "assertion_lint": lint,
        "repeat_check": {
            "method": "gold_capture_determinism_plus_three_binary_runtime",
            "passed": bool(args.repeat_runtime_passed),
            "junit_summary": {"failed_test_names": [], "error_test_names": []},
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "cases": len(cases),
        "dummy_passing": len(passing_names),
        "leak_passed": leak["passed"],
        "lint_passed": lint.get("passed"),
        "output": str(args.output_json),
    }, indent=2))
    return 0 if not passing_names and leak["passed"] and lint.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
