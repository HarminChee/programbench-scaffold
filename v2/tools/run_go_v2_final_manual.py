#!/usr/bin/env python3
"""Run the strict V2 final stages without mounting the oracle bundle for quality scans."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
V2 = ROOT / "v2" / "tools"
COMPAT = ROOT / "v2" / "python_compat"


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def invoke(name: str, cmd: list[str], *, env: dict[str, str], logs: Path, timeout: int) -> int:
    started = dt.datetime.now(dt.timezone.utc)
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
        code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
    write(logs / f"{name}.json", {"cmd": cmd, "returncode": code, "started_at": started.isoformat(), "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(), "stdout": stdout, "stderr": stderr})
    return code


def local_copy(source: Path, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    # Native cp is substantially faster than Python's per-file loop when the
    # source bundle lives on /mnt/c and contains hundreds of fixture files.
    # Keep copytree as a portability fallback.
    copied = subprocess.run(
        ["cp", "-a", f"{source}/.", str(target)],
        text=True,
        capture_output=True,
        check=False,
    )
    if copied.returncode != 0:
        shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=True)
    return target


def remove_skipped(cases: Path, manifest: Path, output: Path) -> bool:
    source = json.loads(cases.read_text(encoding="utf-8"))
    skipped = {str(item.get("name")) for item in json.loads(manifest.read_text(encoding="utf-8")).get("skipped_cases") or []}
    if not skipped:
        return False
    result = dict(source)
    result["cases"] = [case for case in source.get("cases") or [] if str(case.get("name")) not in skipped]
    result["candidate_case_count"] = len(result["cases"])
    result["final_capture_repair"] = {"removed_names": sorted(skipped)}
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True


def remove_named_cases(cases: Path, names: set[str], output: Path, reason: str) -> bool:
    source = json.loads(cases.read_text(encoding="utf-8"))
    if not names:
        return False
    result = dict(source)
    result["cases"] = [case for case in source.get("cases") or [] if str(case.get("name")) not in names]
    removed = len(source.get("cases") or []) - len(result["cases"])
    if not removed:
        return False
    result["candidate_case_count"] = len(result["cases"])
    result["binary_repair"] = {"reason": reason, "removed_names": sorted(names), "removed_count": removed}
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True


def failing_case_names(coverage_data: dict) -> set[str]:
    names: set[str] = set()
    pattern = re.compile(r"\.test_\d+_(.+)$")
    for branch in coverage_data.get("branch_results") or []:
        for binary in branch.get("binary_results") or []:
            junit = binary.get("junit_summary") or {}
            nodes = list(junit.get("filtered_failed_test_names") or []) + list(junit.get("filtered_error_test_names") or [])
            for node in nodes:
                match = pattern.search(str(node))
                if match:
                    names.add(match.group(1))
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("instance_id")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--tasks-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--xdist", default="4")
    parser.add_argument("--case-timeout", default="8")
    parser.add_argument(
        "--resume-cases",
        type=Path,
        help="Resume final capture/coverage/quality from an already filtered or repaired cases JSON.",
    )
    args = parser.parse_args()
    run_root, repo = args.run_root.resolve(), args.instance_id
    repo_run = run_root / repo
    generated, coverage, formal = run_root / "generated", run_root / "coverage", run_root / "formal"
    logs, status = repo_run / "manual_final_logs", repo_run / "manual_final_status.json"
    logs.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(COMPAT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PATH"] = "/usr/local/go1.21.13/bin:" + env.get("PATH", "")
    sanitized = repo_run / "merged" / "v2_expansive_sanitized_candidates.json"
    raw_label, final_label = "v2_expansive_raw", "v2_expansive_final"
    raw_oracle = generated / repo / raw_label / "oracle_tests"
    raw_manifest = raw_oracle / "eval" / "generated_cli_manifest.json"
    raw_quality = generated / repo / raw_label / "evaluation_quality_report.json"
    filtered = repo_run / "merged" / "v2_expansive_filtered_candidates.json"
    if args.resume_cases:
        cases = args.resume_cases.resolve()
        if not cases.is_file():
            raise FileNotFoundError(cases)
        write(status, {"state": "running", "stage": "resume", "cases_json": str(cases)})
    else:
        write(status, {"state": "running", "stage": "sanitize"})
        if invoke("sanitize", [sys.executable, str(V2 / "sanitize_candidates_v2.py"), "--input", str(repo_run / "merged" / "v2_expansive_candidates.json"), "--output", str(sanitized)], env=env, logs=logs, timeout=600):
            return 1
        write(status, {"state": "running", "stage": "raw_capture"})
        if invoke("raw_capture", [sys.executable, str(TOOLS / "programbench_generate_cli_oracle_bundle.py"), repo, "--cases-json", str(sanitized), "--suite-label", raw_label, "--output-root", str(generated), "--work-root", str(args.work_root / repo / "raw_capture"), "--case-timeout", args.case_timeout, "--determinism-reruns", "1", "--overwrite"], env=env, logs=logs, timeout=14400):
            return 1
        write(status, {"state": "running", "stage": "raw_quality"})
        local_raw = local_copy(raw_oracle, args.work_root / repo / "raw_quality_material")
        invoke("raw_quality", [sys.executable, str(V2 / "run_quality_gates_v2.py"), "--oracle-material-root", str(local_raw), "--output-json", str(raw_quality), "--work-root", str(args.work_root / repo / "raw_quality"), "--timeout", "1800", "--overwrite"], env=env, logs=logs, timeout=9000)
        if not raw_quality.is_file():
            return 1
        write(status, {"state": "running", "stage": "filter"})
        if invoke("filter", [sys.executable, str(V2 / "filter_candidates_from_capture_quality_v2.py"), "--candidates", str(sanitized), "--capture-manifest", str(raw_manifest), "--quality-report", str(raw_quality), "--output", str(filtered)], env=env, logs=logs, timeout=600):
            return 1
        cases = filtered
    final_oracle = generated / repo / final_label / "oracle_tests"
    final_manifest = final_oracle / "eval" / "generated_cli_manifest.json"
    coverage_json = coverage / repo / f"{final_label}.go_coverage_summary.json"
    coverage_code, coverage_data = 1, {}
    for validation_attempt in range(4):
        # Expansive suites can expose several disjoint nondeterministic case
        # families one capture at a time (for example Go map order under
        # --no-sort). Keep repairing exact skipped cases instead of abandoning
        # the whole repo after only three passes.
        for capture_attempt in range(16):
            stage_suffix = f"{validation_attempt + 1}_{capture_attempt + 1}"
            write(status, {"state": "running", "stage": f"final_capture_{stage_suffix}"})
            if invoke(f"final_capture_{stage_suffix}", [sys.executable, str(TOOLS / "programbench_generate_cli_oracle_bundle.py"), repo, "--cases-json", str(cases), "--suite-label", final_label, "--output-root", str(generated), "--work-root", str(args.work_root / repo / "final_capture"), "--case-timeout", args.case_timeout, "--determinism-reruns", "1", "--overwrite"], env=env, logs=logs, timeout=14400):
                return 1
            capture = json.loads(final_manifest.read_text(encoding="utf-8"))
            if not capture.get("skipped_cases"):
                break
            repaired = repo_run / "merged" / f"v2_expansive_final_capture_repair_{validation_attempt + 1}_{capture_attempt + 1}.json"
            if not remove_skipped(cases, final_manifest, repaired):
                return 1
            cases = repaired
        if json.loads(final_manifest.read_text(encoding="utf-8")).get("skipped_cases"):
            return 1
        write(status, {"state": "running", "stage": f"coverage_{validation_attempt + 1}"})
        coverage_code = invoke(f"coverage_{validation_attempt + 1}", [sys.executable, str(TOOLS / "programbench_go_coverage_harness.py"), repo, "--tasks-root", str(args.tasks_root), "--oracle-material-root", str(final_oracle), "--suite-label", final_label, "--work-root", str(args.work_root / repo / "coverage"), "--output-root", str(coverage), "--xdist", args.xdist, "--pytest-timeout", "1800", "--overwrite", "--run-native-tests", "--compare-binaries"], env=env, logs=logs, timeout=18000)
        if not coverage_json.is_file():
            return 1
        coverage_data = json.loads(coverage_json.read_text(encoding="utf-8"))
        generated_passed = bool((coverage_data.get("generated_tests") or {}).get("pytest_all_coverage_runs_passed"))
        if coverage_code == 0 and coverage_data.get("all_branch_binary_comparisons_consistent") and generated_passed:
            break
        failed_names = failing_case_names(coverage_data)
        repaired = repo_run / "merged" / f"v2_expansive_binary_repair_{validation_attempt + 1}.json"
        if not remove_named_cases(cases, failed_names, repaired, "three_binary_failure_or_error"):
            break
        cases = repaired
    repeat_exe = args.work_root / repo / "coverage" / repo / final_label / "executable_coverage"
    write(status, {"state": "running", "stage": "final_quality"})
    local_final = local_copy(final_oracle, args.work_root / repo / "final_quality_material")
    final_quality = generated / repo / final_label / "evaluation_quality_report.json"
    quality_code = invoke("final_quality", [sys.executable, str(V2 / "run_quality_gates_v2.py"), "--oracle-material-root", str(local_final), "--output-json", str(final_quality), "--work-root", str(args.work_root / repo / "final_quality"), "--repeat-executable", str(repeat_exe), "--repeat-gocoverdir", str(args.work_root / repo / "final_quality_repeat_cov"), "--timeout", "1800", "--overwrite"], env=env, logs=logs, timeout=9000)
    quality = json.loads(final_quality.read_text(encoding="utf-8")) if final_quality.is_file() else {}
    generated_passed = bool((coverage_data.get("generated_tests") or {}).get("pytest_all_coverage_runs_passed"))
    accepted = coverage_code == 0 and quality_code == 0 and coverage_data.get("all_branch_binary_comparisons_consistent") and generated_passed
    summary = {"status": "passed" if accepted else "needs_repair", "instance_id": repo, "suite_label": final_label, "cases_json": str(cases), "coverage_summary_path": str(coverage_json), "quality_report_path": str(final_quality), "coverage_returncode": coverage_code, "quality_returncode": quality_code, "generated_tests": coverage_data.get("generated_tests"), "native_tests": coverage_data.get("native_tests"), "binary_consistent": coverage_data.get("all_branch_binary_comparisons_consistent"), "all_tests_passed": coverage_data.get("all_filtered_tests_passed"), "quality": {"all_dummies_rejected": quality.get("all_dummies_rejected"), "source_leak_passed": (quality.get("source_leak_scan") or {}).get("passed"), "assertion_lint_passed": (quality.get("assertion_lint") or {}).get("passed")}}
    write(formal / repo / final_label / "pipeline_summary.json", summary)
    write(status, {"state": "completed" if accepted else "needs_repair", "stage": "done", "summary": str(formal / repo / final_label / "pipeline_summary.json")})
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
