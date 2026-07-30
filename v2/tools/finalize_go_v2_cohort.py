#!/usr/bin/env python3
"""Capture, filter, validate, and measure final V2 Go oracle suites."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"
V2_TOOLS = REPO_ROOT / "v2" / "tools"
COMPAT = REPO_ROOT / "v2" / "python_compat"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def remove_skipped_cases(input_path: Path, capture_manifest: Path, output_path: Path) -> int:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    capture = json.loads(capture_manifest.read_text(encoding="utf-8"))
    skipped = {str(item.get("name")) for item in capture.get("skipped_cases") or []}
    kept = [case for case in payload.get("cases") or [] if str(case.get("name")) not in skipped]
    result = dict(payload)
    result["cases"] = kept
    result["candidate_case_count"] = len(kept)
    result["final_capture_repair"] = {"removed_names": sorted(skipped), "input_case_count": len(payload.get("cases") or []), "retained_case_count": len(kept)}
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return len(skipped)


def run_stage(instance_id: str, stage: str, cmd: list[str], logs_dir: Path, status_path: Path, env: dict[str, str], timeout: int) -> int:
    write_json(status_path, {
        "instance_id": instance_id,
        "state": "running",
        "stage": stage,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    started = dt.datetime.now(dt.timezone.utc)
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, env=env, timeout=timeout)
        code, stdout, stderr, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        code, timed_out = 124, True
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
    ended = dt.datetime.now(dt.timezone.utc)
    write_json(logs_dir / f"{stage}.json", {
        "cmd": cmd,
        "returncode": code,
        "timed_out": timed_out,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
        "stdout": stdout,
        "stderr": stderr,
    })
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", action="append", required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--tasks-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--case-timeout", type=int, default=8)
    parser.add_argument("--pytest-timeout", type=int, default=1800)
    parser.add_argument("--xdist", default="4")
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    generated_root = run_root / "generated"
    coverage_root = run_root / "coverage"
    formal_root = run_root / "formal"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(COMPAT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PATH"] = "/usr/local/go1.21.13/bin:" + env.get("PATH", "")
    cohort_results = []

    for instance_id in args.instance_id:
        repo_run = run_root / instance_id
        logs_dir = repo_run / "finalize_logs"
        status_path = repo_run / "finalize_status.json"
        logs_dir.mkdir(parents=True, exist_ok=True)
        candidates = repo_run / "merged" / "v2_expansive_candidates.json"
        if not candidates.is_file():
            write_json(status_path, {"instance_id": instance_id, "state": "failed", "stage": "preflight", "detail": "merged candidates missing"})
            cohort_results.append({"instance_id": instance_id, "status": "failed", "reason": "merged candidates missing"})
            continue

        sanitized = repo_run / "merged" / "v2_expansive_sanitized_candidates.json"
        if not sanitized.is_file():
            code = run_stage(instance_id, "sanitize_candidates", [
                sys.executable, str(V2_TOOLS / "sanitize_candidates_v2.py"),
                "--input", str(candidates), "--output", str(sanitized),
            ], logs_dir, status_path, env, 600)
            if code != 0 or not sanitized.is_file():
                write_json(status_path, {"instance_id": instance_id, "state": "failed", "stage": "sanitize_candidates", "returncode": code})
                cohort_results.append({"instance_id": instance_id, "status": "failed", "stage": "sanitize_candidates"})
                continue

        raw_label = "v2_expansive_raw"
        raw_oracle = generated_root / instance_id / raw_label / "oracle_tests"
        raw_manifest = raw_oracle / "eval" / "generated_cli_manifest.json"
        raw_quality = generated_root / instance_id / raw_label / "evaluation_quality_report.json"
        filtered = repo_run / "merged" / "v2_expansive_filtered_candidates.json"
        final_label = "v2_expansive_final"
        final_summary = formal_root / instance_id / final_label / "pipeline_summary.json"

        if not raw_manifest.is_file():
            code = run_stage(instance_id, "capture_raw", [
                sys.executable, str(TOOLS / "programbench_generate_cli_oracle_bundle.py"), instance_id,
                "--cases-json", str(sanitized), "--suite-label", raw_label,
                "--output-root", str(generated_root), "--work-root", str(args.work_root / instance_id / "capture_raw"),
                "--case-timeout", str(args.case_timeout), "--determinism-reruns", "1", "--overwrite",
            ], logs_dir, status_path, env, 7200)
            if code != 0 or not raw_manifest.is_file():
                write_json(status_path, {"instance_id": instance_id, "state": "failed", "stage": "capture_raw", "returncode": code})
                cohort_results.append({"instance_id": instance_id, "status": "failed", "stage": "capture_raw"})
                continue

        if not raw_quality.is_file():
            code = run_stage(instance_id, "quality_raw", [
                sys.executable, str(V2_TOOLS / "run_quality_gates_v2.py"),
                "--oracle-material-root", str(raw_oracle), "--output-json", str(raw_quality),
                "--work-root", str(args.work_root / instance_id / "quality_raw"),
                "--timeout", str(args.pytest_timeout), "--overwrite",
            ], logs_dir, status_path, env, 7200)
            if not raw_quality.is_file():
                write_json(status_path, {"instance_id": instance_id, "state": "failed", "stage": "quality_raw", "returncode": code})
                cohort_results.append({"instance_id": instance_id, "status": "failed", "stage": "quality_raw"})
                continue

        if not filtered.is_file():
            code = run_stage(instance_id, "filter_candidates", [
                sys.executable, str(V2_TOOLS / "filter_candidates_from_capture_quality_v2.py"),
                "--candidates", str(sanitized), "--capture-manifest", str(raw_manifest),
                "--quality-report", str(raw_quality), "--output", str(filtered),
            ], logs_dir, status_path, env, 600)
            if code != 0 or not filtered.is_file():
                write_json(status_path, {"instance_id": instance_id, "state": "failed", "stage": "filter_candidates", "returncode": code})
                cohort_results.append({"instance_id": instance_id, "status": "failed", "stage": "filter_candidates"})
                continue

        final_cases = filtered
        summary = json.loads(final_summary.read_text(encoding="utf-8")) if final_summary.is_file() else {}
        for attempt in range(3):
            if summary.get("status") == "passed":
                break
            code = run_stage(instance_id, f"final_pipeline_{attempt + 1}", [
                sys.executable, str(TOOLS / "programbench_run_pb_style_go_oracle_pipeline.py"), instance_id,
                "--tasks-root", str(args.tasks_root), "--suite-label", final_label,
                "--cases-json", str(final_cases), "--work-root", str(args.work_root / "formal"),
                "--output-root", str(formal_root), "--generated-output-root", str(generated_root),
                "--coverage-output-root", str(coverage_root), "--case-timeout", str(args.case_timeout),
                "--determinism-reruns", "1", "--xdist", str(args.xdist),
                "--pytest-timeout", str(args.pytest_timeout), "--overwrite",
            ], logs_dir, status_path, env, 14400)
            if not final_summary.is_file():
                write_json(status_path, {"instance_id": instance_id, "state": "failed", "stage": "final_pipeline", "returncode": code})
                cohort_results.append({"instance_id": instance_id, "status": "failed", "stage": "final_pipeline"})
                continue
            summary = json.loads(final_summary.read_text(encoding="utf-8"))
            failed = summary.get("failed_step") or {}
            gate = failed.get("capture_gate") or {}
            manifest_text = gate.get("manifest_path")
            if failed.get("name") != "require_all_candidate_cases_captured" or not manifest_text:
                break
            manifest = Path(str(manifest_text))
            if not manifest.is_file():
                break
            repaired = repo_run / "merged" / f"v2_expansive_final_capture_repair_{attempt + 1}.json"
            removed = remove_skipped_cases(final_cases, manifest, repaired)
            if not removed:
                break
            final_cases = repaired
        state = "completed" if summary.get("status") == "passed" else "needs_repair"
        write_json(status_path, {"instance_id": instance_id, "state": state, "stage": "done", "pipeline_status": summary.get("status"), "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()})
        cohort_results.append({"instance_id": instance_id, "status": state, "pipeline_summary": str(final_summary)})

    write_json(run_root / "finalize_cohort_status.json", {"results": cohort_results, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    return 0 if all(item["status"] == "completed" for item in cohort_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
