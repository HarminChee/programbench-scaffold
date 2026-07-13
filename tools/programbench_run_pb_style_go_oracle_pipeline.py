#!/usr/bin/env python3
"""Run the PB-style Go oracle reproduction pipeline for one ProgramBench task."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_ROOT = Path("/home/harminchee/codex-workspaces/ProgramBench/src/programbench/data/tasks")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_step(name: str, cmd: list[str], logs_dir: Path, *, cwd: Path = REPO_ROOT, timeout: int | None = None) -> dict[str, Any]:
    started = dt.datetime.now(dt.UTC)
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
    ended = dt.datetime.now(dt.UTC)
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


def summary_from_coverage(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    data = read_json(path)
    branch = (data.get("branch_results") or [{}])[0]
    generated = data.get("generated_tests") or {}
    native = data.get("native_tests") or {}
    generated_cov = generated.get("total_statement_coverage_percent", generated.get("statement_coverage_percent"))
    native_cov = native.get("total_statement_coverage_percent", native.get("statement_coverage_percent"))
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
        "binary_behavior_consistent": data.get("all_branch_binary_comparisons_consistent"),
        "junit_summary": junit_summary,
    }


def summary_from_quality(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    data = read_json(path)
    assertion = data.get("assertion_lint") or {}
    repeat = data.get("repeat_check") or {}
    return {
        "exists": True,
        "all_dummies_rejected": data.get("all_dummies_rejected"),
        "source_leak_passed": (data.get("source_leak_scan") or {}).get("passed"),
        "assertion_lint_passed": assertion.get("passed"),
        "assertion_lint_high_count": assertion.get("high_count"),
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
    parser.add_argument("--case-timeout", type=int, default=10)
    parser.add_argument("--determinism-reruns", type=int, default=2)
    parser.add_argument("--xdist", default="1")
    parser.add_argument("--pytest-timeout", type=int, default=900)
    parser.add_argument("--skip-native-tests", action="store_true")
    parser.add_argument("--skip-compare-binaries", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

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
    coverage_work_root = work_root / "coverage"
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
    step = run_step("run_go_coverage_harness", cmd, logs_dir, timeout=3600)
    steps.append(step)
    if step["returncode"] != 0:
        write_json(run_root / "pipeline_summary.json", {"status": "failed", "failed_step": step, "steps": steps})
        return step["returncode"]

    coverage_summary = (
        args.coverage_output_root / args.instance_id / f"{args.suite_label}.go_coverage_summary.json"
    ).resolve()
    repeat_executable = coverage_work_root / args.instance_id / args.suite_label / "executable_coverage"
    quality_json = args.generated_output_root / args.instance_id / args.suite_label / "evaluation_quality_report.json"
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
        "--repeat-gocoverdir",
        str(work_root / "quality_gates" / "repeat_cov"),
        "--timeout",
        str(args.pytest_timeout),
        "--overwrite",
    ]
    step = run_step("run_generated_oracle_quality_gates", cmd, logs_dir, timeout=2400)
    steps.append(step)

    cases_payload = read_json(cases_json)
    summary = {
        "status": "passed" if all(item["returncode"] == 0 for item in steps) else "failed",
        "instance_id": args.instance_id,
        "suite_label": args.suite_label,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "cases_json": str(cases_json),
        "case_count": len(cases_payload.get("cases") or []),
        "oracle_root": str(oracle_root),
        "coverage_summary_path": str(coverage_summary),
        "quality_report_path": str(quality_json),
        "coverage": summary_from_coverage(coverage_summary),
        "quality": summary_from_quality(quality_json),
        "steps": steps,
    }
    write_json(run_root / "pipeline_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
