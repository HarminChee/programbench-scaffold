#!/usr/bin/env python3
"""Run the PB-style oracle reproduction pipeline for Go, Rust, and C/C++."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from programbench_go_coverage_harness import parse_simple_yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_ROOT = Path("/home/harminchee/codex-workspaces/ProgramBench/src/programbench/data/tasks")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_step(name: str, cmd: list[str], logs_dir: Path, *, cwd: Path = REPO_ROOT, timeout: int | None = None) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc)
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        returncode = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        timed_out = True
    ended = dt.datetime.now(dt.timezone.utc)
    payload = {
        "name": name,
        "cmd": cmd,
        "cwd": str(cwd),
        "returncode": returncode,
        "timed_out": timed_out,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
        "stdout_tail": stdout[-8000:],
        "stderr_tail": stderr[-8000:],
    }
    write_json(logs_dir / f"{name}.json", {**payload, "stdout": stdout, "stderr": stderr})
    return payload


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def scaled_quality_suite_timeout(case_count: int, pytest_timeout: int) -> int:
    """Give whole-suite dummy/repeat runs time proportional to suite size."""

    return max(int(pytest_timeout), min(1800, 60 + max(0, int(case_count))))


def strict_capture_gate(oracle_root: Path) -> dict[str, Any]:
    """Require every proposed case to survive deterministic gold capture."""

    manifest_path = oracle_root / "eval" / "generated_cli_manifest.json"
    if not manifest_path.is_file():
        return {"passed": False, "manifest_path": str(manifest_path), "reason": "manifest_missing"}
    manifest = read_json(manifest_path)
    candidate_count = int(manifest.get("candidate_case_count") or 0)
    captured_count = int(manifest.get("case_count") or 0)
    skipped = manifest.get("skipped_cases") or []
    return {
        "passed": candidate_count == captured_count and not skipped,
        "manifest_path": str(manifest_path),
        "candidate_case_count": candidate_count,
        "captured_case_count": captured_count,
        "skipped_case_count": len(skipped),
        "skipped_cases": skipped,
    }


def summary_from_coverage(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    data = read_json(path)
    if data.get("language") in {"rs", "rust", "c", "cpp", "c++"}:
        coverage = data.get("coverage") or {}
        native = data.get("native") or {}
        rust = data.get("language") in {"rs", "rust"}
        generated_line = (coverage.get("lines") or {}).get("percent") if rust else coverage.get("line_percent")
        native_line = (native.get("lines") or {}).get("percent") if rust else native.get("line_percent")
        branch = (data.get("branch_results") or [{}])[0]
        junit_summary = {}
        for result in branch.get("binary_results") or []:
            if result.get("label") == "coverage":
                junit_summary = result.get("junit_summary") or {}
                break
        return {
            "exists": True,
            "repository": data.get("repository"),
            "commit": data.get("commit"),
            "language": data.get("language"),
            "coverage_metric": "llvm executable-line" if rust else "gcov executable-line",
            "generated_line_coverage": generated_line,
            "native_line_coverage": native_line,
            "per_file_coverage": coverage.get("files") or [],
            "binary_behavior_consistent": data.get("all_branch_binary_comparisons_consistent"),
            "all_filtered_tests_passed": data.get("all_filtered_tests_passed"),
            "junit_summary": junit_summary,
            "coverage_binary": data.get("coverage_binary"),
        }
    branch = (data.get("branch_results") or [{}])[0]
    generated = data.get("generated_tests") or {}
    native = data.get("native_tests") or {}
    generated_cov = generated.get("total_statement_coverage_percent", generated.get("statement_coverage_percent"))
    native_cov = native.get("total_statement_coverage_percent", native.get("statement_coverage_percent"))
    generated_line_cov = generated.get("line_coverage_percent")
    native_line_cov = native.get("line_coverage_percent")
    junit_summary = {}
    for result in branch.get("binary_results") or []:
        if result.get("label") == "coverage":
            junit_summary = result.get("junit_summary") or {}
            break
    return {
        "exists": True,
        "repository": data.get("repository"),
        "commit": data.get("commit"),
        "go_build_package": data.get("go_build_package"),
        "go_coverpkg": data.get("go_coverpkg"),
        "generated_go_statement_coverage": generated_cov,
        "native_go_statement_coverage": native_cov,
        "generated_go_line_coverage": generated_line_cov,
        "native_go_line_coverage": native_line_cov,
        "binary_behavior_consistent": data.get("all_branch_binary_comparisons_consistent"),
        "junit_summary": junit_summary,
    }


def summary_from_quality(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    data = read_json(path)
    assertion = data.get("assertion_lint") or {}
    repeat = data.get("repeat_check") or {}
    dummy_results = data.get("dummy_reject") or []
    return {
        "exists": True,
        "all_dummies_rejected": data.get("all_dummies_rejected"),
        "all_tests_reject_all_dummies": data.get("all_tests_reject_all_dummies"),
        "dummy_passing_test_count": data.get("dummy_passing_test_count", 0),
        "dummy_passing_test_names": data.get("dummy_passing_test_names") or [],
        "dummy_results": [
            {
                "kind": item.get("kind"),
                "all_tests_rejected": item.get("all_tests_rejected"),
                "passing_test_count": item.get("passing_test_count", 0),
                "passing_test_names": item.get("passing_test_names") or [],
            }
            for item in dummy_results
        ],
        "source_leak_passed": (data.get("source_leak_scan") or {}).get("passed"),
        "assertion_lint_passed": assertion.get("passed"),
        "assertion_lint_high_count": assertion.get("high_count"),
        "assertion_lint_medium_count": assertion.get("medium_count"),
        "assertion_lint_low_count": assertion.get("low_count"),
        "assertion_lint_issues": assertion.get("issues") or [],
        "repeat_returncode": repeat.get("pytest_returncode") if repeat else None,
        "repeat_junit_summary": repeat.get("junit_summary") if repeat else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id")
    parser.add_argument("--tasks-root", type=Path, default=DEFAULT_TASKS_ROOT)
    parser.add_argument("--suite-label", default="pb_source_aware_go_v1")
    parser.add_argument("--cases-json", type=Path)
    parser.add_argument("--max-cases", type=int, default=180)
    parser.add_argument("--work-root", type=Path, default=Path("/tmp/programbench_pb_style_go_oracle_pipeline"))
    parser.add_argument("--output-root", type=Path, default=REPO_ROOT / "reports/programbench_pb_style_go_oracle_pipeline")
    parser.add_argument("--generated-output-root", type=Path, default=REPO_ROOT / "reports/programbench_pb_style_generated_oracles")
    parser.add_argument("--coverage-output-root", type=Path, default=REPO_ROOT / "reports/programbench_pb_style_go_coverage")
    parser.add_argument("--case-timeout", type=int, default=4)
    parser.add_argument("--determinism-reruns", type=int, default=2)
    parser.add_argument("--xdist", default="1")
    parser.add_argument(
        "--coverage-xdist",
        default="1",
        help="Worker count for instrumented native binaries; keep at 1 to avoid concurrent gcda writes.",
    )
    parser.add_argument("--pytest-timeout", type=int, default=900)
    parser.add_argument("--binary-name", help="Override the repository-name default for C/C++ CLI builds.")
    parser.add_argument("--skip-native-tests", action="store_true")
    parser.add_argument("--skip-compare-binaries", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    task_meta = parse_simple_yaml(args.tasks_root / args.instance_id / "task.yaml")
    language = str(task_meta.get("language") or "").lower()

    work_root = args.work_root.expanduser().resolve() / args.instance_id / args.suite_label
    if work_root.exists():
        if not args.overwrite:
            raise FileExistsError(f"Work root exists: {work_root}")
        shutil.rmtree(work_root)
    logs_dir = work_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    run_root = (args.output_root / args.instance_id / args.suite_label).resolve()
    if run_root.exists() and args.overwrite:
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True, exist_ok=True)

    cases_json = args.cases_json
    steps: list[dict[str, Any]] = []
    if cases_json is None:
        cases_json = run_root / "candidate_cases.json"
        cmd = [
            sys.executable,
            "tools/programbench_generate_source_aware_cli_cases.py",
            args.instance_id,
            "--tasks-root",
            str(args.tasks_root),
            "--work-root",
            str(work_root / "case_miner"),
            "--output-json",
            str(cases_json),
            "--profile",
            args.suite_label,
            "--max-cases",
            str(args.max_cases),
            "--overwrite",
        ]
        step = run_step("generate_source_aware_cases", cmd, logs_dir, timeout=1200)
        steps.append(step)
        if step["returncode"] != 0:
            write_json(run_root / "pipeline_summary.json", {"status": "failed", "failed_step": step, "steps": steps})
            return step["returncode"]
    else:
        cases_json = cases_json.expanduser().resolve()

    cmd = [
        sys.executable,
        "tools/programbench_generate_cli_oracle_bundle.py",
        args.instance_id,
        "--cases-json",
        str(cases_json),
        "--suite-label",
        args.suite_label,
        "--output-root",
        str(args.generated_output_root),
        "--work-root",
        str(work_root / "capture"),
        "--case-timeout",
        str(args.case_timeout),
        "--determinism-reruns",
        str(args.determinism_reruns),
        "--overwrite",
    ]
    step = run_step("capture_reference_oracle_bundle", cmd, logs_dir, timeout=1800)
    steps.append(step)
    if step["returncode"] != 0:
        write_json(run_root / "pipeline_summary.json", {"status": "failed", "failed_step": step, "steps": steps})
        return step["returncode"]

    oracle_root = (args.generated_output_root / args.instance_id / args.suite_label / "oracle_tests").resolve()
    capture_gate = strict_capture_gate(oracle_root)
    if not capture_gate["passed"]:
        failed_step = {
            "name": "require_all_candidate_cases_captured",
            "returncode": 1,
            "timed_out": False,
            "capture_gate": capture_gate,
        }
        steps.append(failed_step)
        write_json(
            run_root / "pipeline_summary.json",
            {
                "status": "failed",
                "instance_id": args.instance_id,
                "suite_label": args.suite_label,
                "cases_json": str(cases_json),
                "capture_gate": capture_gate,
                "failed_step": failed_step,
                "steps": steps,
            },
        )
        return 1
    coverage_work_root = work_root / "coverage"
    if language == "go":
        cmd = [
            sys.executable,
            "tools/programbench_go_coverage_harness.py",
            args.instance_id,
            "--tasks-root",
            str(args.tasks_root),
            "--oracle-material-root",
            str(oracle_root),
            "--suite-label",
            args.suite_label,
            "--work-root",
            str(coverage_work_root),
            "--output-root",
            str(args.coverage_output_root),
            "--xdist",
            str(args.xdist),
            "--pytest-timeout",
            str(args.pytest_timeout),
            "--overwrite",
        ]
        if not args.skip_native_tests:
            cmd.append("--run-native-tests")
        if not args.skip_compare_binaries:
            cmd.append("--compare-binaries")
        coverage_summary = (
            args.coverage_output_root / args.instance_id / f"{args.suite_label}.go_coverage_summary.json"
        ).resolve()
        coverage_step_name = "run_go_coverage_harness"
    elif language in {"rs", "rust", "c", "cpp", "c++"}:
        coverage_summary = (
            args.coverage_output_root / args.instance_id / f"{args.suite_label}.native_coverage_summary.json"
        ).resolve()
        configured_cache = os.getenv("PROGRAMBENCH_NATIVE_BUILD_CACHE", "").strip()
        build_cache = (
            Path(configured_cache).expanduser().resolve()
            if configured_cache
            else (args.coverage_output_root / args.instance_id / "_native_build_cache").resolve()
        )
        cache_ready = (build_cache / "source_build").is_dir() and (build_cache / "coverage_build").is_dir()
        native_work = coverage_work_root if cache_ready else build_cache
        cmd = [
            sys.executable,
            "tools/programbench_native_coverage_harness.py",
            args.instance_id,
            "--tasks-root",
            str(args.tasks_root),
            "--oracle-material-root",
            str(oracle_root),
            "--suite-label",
            args.suite_label,
            "--work-root",
            str(native_work),
            "--output-json",
            str(coverage_summary),
            "--pytest-python",
            sys.executable,
            "--xdist",
            str(args.xdist),
            "--coverage-xdist",
            str(args.coverage_xdist),
            "--pytest-timeout",
            str(args.pytest_timeout),
            "--skip-native",
            "--overwrite",
        ]
        if cache_ready:
            cmd.extend(["--reuse-build-root", str(build_cache)])
        if args.binary_name:
            cmd.extend(["--binary-name", args.binary_name])
        coverage_step_name = "run_native_coverage_harness"
    else:
        raise ValueError(f"unsupported ProgramBench language: {language!r}")
    step = run_step(coverage_step_name, cmd, logs_dir, timeout=7200)
    steps.append(step)
    # The coverage harness returns non-zero when generated tests expose a
    # behavioral mismatch.  That is useful repair evidence, not necessarily
    # an infrastructure failure.  Continue through the quality gates whenever
    # the harness managed to write its structured summary and executable.
    if step["returncode"] != 0 and not coverage_summary.exists():
        write_json(
            run_root / "pipeline_summary.json",
            {
                "status": "failed",
                "failed_step": step,
                "coverage_summary_path": None,
                "coverage": {"exists": False},
                "steps": steps,
            },
        )
        return step["returncode"]
    coverage_data = read_json(coverage_summary)
    repeat_executable = (
        coverage_work_root / args.instance_id / args.suite_label / "executable_coverage"
        if language == "go"
        else Path(str(coverage_data["coverage_binary"]))
    )
    quality_json = args.generated_output_root / args.instance_id / args.suite_label / "evaluation_quality_report.json"
    quality_case_count = len((read_json(cases_json).get("cases") or []))
    quality_suite_timeout = scaled_quality_suite_timeout(quality_case_count, args.pytest_timeout)
    cmd = [
        sys.executable,
        "tools/programbench_run_generated_oracle_quality_gates.py",
        "--oracle-material-root",
        str(oracle_root),
        "--output-json",
        str(quality_json),
        "--work-root",
        str(work_root / "quality_gates"),
        "--repeat-executable",
        str(repeat_executable),
        "--timeout",
        str(quality_suite_timeout),
        "--overwrite",
    ]
    if language == "go":
        cmd.extend(["--repeat-gocoverdir", str(work_root / "quality_gates" / "repeat_cov")])
    step = run_step("run_generated_oracle_quality_gates", cmd, logs_dir, timeout=2400)
    steps.append(step)

    cases_payload = read_json(cases_json)
    summary = {
        "status": "passed" if all(item["returncode"] == 0 for item in steps) else "failed",
        "instance_id": args.instance_id,
        "suite_label": args.suite_label,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "cases_json": str(cases_json),
        "case_count": len(cases_payload.get("cases") or []),
        "oracle_root": str(oracle_root),
        "capture_gate": capture_gate,
        "coverage_summary_path": str(coverage_summary),
        "quality_report_path": str(quality_json),
        "coverage": summary_from_coverage(coverage_summary),
        "quality": summary_from_quality(quality_json),
        "failed_step": next((item for item in steps if item["returncode"] != 0), None),
        "steps": steps,
    }
    write_json(run_root / "pipeline_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
