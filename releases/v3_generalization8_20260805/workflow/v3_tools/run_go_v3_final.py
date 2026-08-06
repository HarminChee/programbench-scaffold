#!/usr/bin/env python3
"""Run V3 Go sanitize, gold capture, quality, three-binary coverage, and stop gates."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
V2 = ROOT / "v2" / "tools"
V3 = ROOT / "v3" / "tools"
COMPAT = ROOT / "v2" / "python_compat"


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def invoke(name: str, cmd: list[str], logs: Path, env: dict[str, str], timeout: int) -> int:
    started = dt.datetime.now(dt.timezone.utc)
    monotonic_started = time.perf_counter()
    try:
        result = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
        code, stdout, stderr, timed_out = result.returncode, result.stdout, result.stderr, False
    except subprocess.TimeoutExpired as exc:
        code, timed_out = 124, True
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
    ended = dt.datetime.now(dt.timezone.utc)
    write(logs / f"{name}.json", {
        "cmd": cmd, "returncode": code, "timed_out": timed_out,
        "started_at": started.isoformat(), "ended_at": ended.isoformat(),
        "duration_seconds": round(time.perf_counter() - monotonic_started, 6),
        "stdout": stdout, "stderr": stderr,
    })
    return code


def filter_skipped(candidates: Path, manifest: Path, output: Path) -> int:
    source = json.loads(candidates.read_text())
    capture = json.loads(manifest.read_text())
    skipped = {str(item.get("name")) for item in capture.get("skipped_cases") or []}
    result = dict(source)
    result["cases"] = [case for case in source.get("cases") or [] if str(case.get("name")) not in skipped]
    result["candidate_case_count"] = len(result["cases"])
    result["capture_repair"] = {"removed_names": sorted(skipped), "retained_repetitions": True}
    write(output, result)
    return len(skipped)


def failed_names(coverage: dict) -> set[str]:
    names, pattern = set(), re.compile(r"\.test_\d+_(.+)$")
    for branch in coverage.get("branch_results") or []:
        for binary in branch.get("binary_results") or []:
            junit = binary.get("junit_summary") or {}
            for node in [
                *(junit.get("filtered_failed_test_names") or []),
                *(junit.get("filtered_error_test_names") or []),
            ]:
                match = pattern.search(str(node))
                if match:
                    names.add(match.group(1))
    return names


def remove_names(candidates: Path, names: set[str], output: Path) -> int:
    source = json.loads(candidates.read_text())
    result = dict(source)
    result["cases"] = [case for case in source.get("cases") or [] if str(case.get("name")) not in names]
    result["candidate_case_count"] = len(result["cases"])
    result["binary_repair"] = {"removed_names": sorted(names), "retained_repetitions": True}
    write(output, result)
    return len(source.get("cases") or []) - len(result["cases"])


def copy_material(source: Path, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    pack = subprocess.Popen(
        ["tar", "-C", str(source), "-cf", "-", "."],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert pack.stdout is not None
    unpack = subprocess.run(
        ["tar", "-C", str(target), "-xf", "-"],
        stdin=pack.stdout,
        capture_output=True,
    )
    pack.stdout.close()
    pack_stderr = pack.stderr.read() if pack.stderr is not None else b""
    pack_code = pack.wait()
    if pack_code or unpack.returncode:
        shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=True)
    return target


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("instance_id")
    ap.add_argument("--run-root", type=Path, required=True)
    ap.add_argument("--tasks-root", type=Path, required=True)
    ap.add_argument("--work-root", type=Path, required=True)
    ap.add_argument("--xdist", default="4")
    ap.add_argument("--case-timeout", default="10")
    ap.add_argument("--qemu-max-cases", type=int, default=80)
    ap.add_argument(
        "--behavior-cap",
        default="5",
        help="Maximum representatives retained per identical behavior group; 0 disables the cap.",
    )
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    run_root, instance = args.run_root.resolve(), args.instance_id
    repo = run_root / instance
    logs, status = repo / "final_logs", repo / "final_status.json"
    logs.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(COMPAT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PATH"] = "/usr/local/go1.21.13/bin:" + env.get("PATH", "")
    merged = repo / "merged" / "v3_candidates.json"
    sanitized_base = repo / "merged" / "v3_sanitized_base.json"
    fixture_normalized = repo / "merged" / "v3_fixture_normalized.json"
    sanitized = repo / "merged" / "v3_sanitized.json"
    filtered = repo / "merged" / "v3_quality_filtered.json"
    low_value_filtered = repo / "merged" / "v3_low_value_filtered.json"
    low_value_report = repo / "low_value_filter_report.json"
    generated_root, coverage_root = run_root / "generated", run_root / "coverage"
    raw_label, final_label = "v3_raw", "v3_final"
    raw_oracle = generated_root / instance / raw_label / "oracle_tests"
    final_oracle = generated_root / instance / final_label / "oracle_tests"
    raw_manifest = raw_oracle / "eval" / "generated_cli_manifest.json"
    final_manifest = final_oracle / "eval" / "generated_cli_manifest.json"
    raw_quality = generated_root / instance / raw_label / "evaluation_quality_report.json"
    annotations = repo / "case_annotations.json"
    annotation_queue = repo / "case_annotation_queue.csv"
    final_quality = generated_root / instance / final_label / "evaluation_quality_report.json"
    coverage_json = coverage_root / instance / f"{final_label}.go_coverage_summary.json"
    write(status, {"state": "running", "stage": "sanitize"})
    if not (args.resume and sanitized_base.is_file()):
        if invoke("sanitize", [sys.executable, str(V2 / "sanitize_candidates_v2.py"), "--input", str(merged), "--output", str(sanitized_base)], logs, env, 900):
            return 1
    plan_spec = json.loads((repo / "plan" / "instance_spec.json").read_text(encoding="utf-8"))
    if not (args.resume and fixture_normalized.is_file()):
        if invoke("materialize_source_fixtures", [
            sys.executable, str(V3 / "materialize_source_fixtures_v3.py"),
            "--input", str(sanitized_base), "--source-dir", str(plan_spec["source_dir"]),
            "--output", str(fixture_normalized),
        ], logs, env, 900):
            return 1
    if not (args.resume and sanitized.is_file()):
        if invoke("normalize_fixture_dsl", [
            sys.executable, str(V3 / "normalize_fixture_dsl_v3.py"),
            "--input", str(fixture_normalized), "--output", str(sanitized),
        ], logs, env, 900):
            return 1
    if not (args.resume and raw_manifest.is_file()):
        if invoke("raw_capture", [
            sys.executable, str(TOOLS / "programbench_generate_cli_oracle_bundle.py"), instance,
            "--cases-json", str(sanitized), "--suite-label", raw_label,
            "--output-root", str(generated_root), "--work-root", str(args.work_root / instance / "raw_capture"),
            "--case-timeout", args.case_timeout, "--determinism-reruns", "1", "--overwrite",
        ], logs, env, 21600):
            return 1
    reusable_quality = False
    if args.resume and raw_quality.is_file() and filtered.is_file():
        try:
            reusable_quality = json.loads(raw_quality.read_text()).get("schema") == (
                "programbench_oracle_gym_v3_fast_quality"
            )
        except (OSError, json.JSONDecodeError):
            reusable_quality = False
    if not reusable_quality:
        local_raw = copy_material(raw_oracle, args.work_root / instance / "raw_quality_material")
        invoke("raw_quality", [
            sys.executable, str(V3 / "fast_quality_gates_v3.py"),
            "--oracle-material-root", str(local_raw), "--output-json", str(raw_quality),
        ], logs, env, 3600)
        if not raw_quality.is_file():
            return 1
        if invoke("quality_filter", [
            sys.executable, str(V2 / "filter_candidates_from_capture_quality_v2.py"),
            "--candidates", str(sanitized), "--capture-manifest", str(raw_manifest),
            "--quality-report", str(raw_quality), "--output", str(filtered),
        ], logs, env, 900):
            return 1
    if not (args.resume and annotations.is_file()):
        annotation_command = [
            sys.executable, str(V3 / "build_case_annotations_v3.py"),
            "--capture-manifest", str(raw_manifest),
            "--output-json", str(annotations), "--output-csv", str(annotation_queue),
        ]
        signal_path = repo / "case_signals.json"
        human_path = repo / "human_case_annotations.csv"
        agent_path = repo / "agent_case_annotations.json"
        if signal_path.is_file():
            annotation_command += ["--case-signals", str(signal_path)]
        if human_path.is_file():
            annotation_command += ["--human-annotations", str(human_path)]
        if agent_path.is_file():
            annotation_command += ["--agent-annotations", str(agent_path)]
        if invoke("case_annotations", annotation_command, logs, env, 900):
            return 1
    if not (args.resume and low_value_filtered.is_file()):
        if invoke("low_value_filter", [
            sys.executable, str(V3 / "filter_low_value_cases_v3.py"),
            "--candidates", str(filtered), "--capture-manifest", str(raw_manifest),
            "--output", str(low_value_filtered), "--report", str(low_value_report),
            "--behavior-cap", str(args.behavior_cap),
            "--annotations", str(annotations),
        ], logs, env, 900):
            return 1
    cases = low_value_filtered
    repair_offset = 0
    if args.resume:
        prior_repairs = sorted(
            (repo / "merged").glob("v3_binary_repair_*.json"),
            key=lambda path: int(path.stem.rsplit("_", 1)[-1]),
        )
        if prior_repairs:
            cases = prior_repairs[-1]
            repair_offset = int(cases.stem.rsplit("_", 1)[-1])
    for attempt in range(1):
        reusable_final = (
            args.resume
            and attempt == 0
            and final_manifest.is_file()
        )
        if not reusable_final:
            if invoke(f"final_capture_{attempt + 1}", [
                sys.executable, str(TOOLS / "programbench_generate_cli_oracle_bundle.py"), instance,
                "--cases-json", str(cases), "--suite-label", final_label,
                "--output-root", str(generated_root), "--work-root", str(args.work_root / instance / "final_capture"),
                "--case-timeout", args.case_timeout, "--determinism-reruns", "1", "--overwrite",
            ], logs, env, 21600):
                return 1
        skipped = json.loads(final_manifest.read_text()).get("skipped_cases") or []
        if skipped:
            repaired = repo / "merged" / f"v3_capture_repair_{attempt + 1}.json"
            if not filter_skipped(cases, final_manifest, repaired):
                return 1
            cases = repaired
        break
    coverage_data = {}
    local_final = copy_material(
        final_oracle, args.work_root / instance / "final_coverage_material"
    )
    # Allow four repair/removal rounds, then always perform one final
    # validation run on the fourth repaired suite. Previously the loop ended
    # immediately after that recapture and wrote a stale pre-repair failure
    # into pipeline_summary.json.
    max_binary_repairs = 4
    for attempt in range(max_binary_repairs + 1):
        invoke(f"coverage_{attempt + 1}", [
            sys.executable, str(TOOLS / "programbench_go_coverage_harness.py"), instance,
            "--tasks-root", str(args.tasks_root), "--oracle-material-root", str(local_final),
            "--suite-label", final_label, "--work-root", str(args.work_root / instance / "coverage"),
            "--output-root", str(coverage_root), "--xdist", args.xdist, "--pytest-timeout", "2400",
            "--overwrite", "--run-native-tests", "--compare-binaries",
        ], logs, env, 28800)
        if not coverage_json.is_file():
            return 1
        coverage_data = json.loads(coverage_json.read_text())
        generated = coverage_data.get("generated_tests") or {}
        if coverage_data.get("all_branch_binary_comparisons_consistent") and generated.get("pytest_all_coverage_runs_passed"):
            break
        if attempt == max_binary_repairs:
            break
        names = failed_names(coverage_data)
        repaired = repo / "merged" / f"v3_binary_repair_{repair_offset + attempt + 1}.json"
        if not remove_names(cases, names, repaired):
            break
        cases = repaired
        if invoke(f"binary_repair_recapture_{attempt + 1}", [
            sys.executable, str(TOOLS / "programbench_generate_cli_oracle_bundle.py"), instance,
            "--cases-json", str(cases), "--suite-label", final_label,
            "--output-root", str(generated_root), "--work-root", str(args.work_root / instance / "final_capture"),
            "--case-timeout", args.case_timeout, "--determinism-reruns", "1", "--overwrite",
        ], logs, env, 21600):
            return 1
        local_final = copy_material(
            final_oracle, args.work_root / instance / "final_coverage_material"
        )
    quality_code = invoke("final_quality", [
        sys.executable, str(V3 / "fast_quality_gates_v3.py"),
        "--oracle-material-root", str(local_final), "--output-json", str(final_quality),
        "--repeat-runtime-passed",
    ], logs, env, 3600)
    qemu_path = repo / "coverage" / "v3_final.dynamic_path_metrics.json"
    source_executable = None
    for branch in coverage_data.get("branch_results") or []:
        for binary in branch.get("binary_results") or []:
            if binary.get("label") != "source":
                continue
            candidate = Path(str(binary.get("execution_workspace") or "")) / "executable"
            if candidate.is_file():
                source_executable = candidate
                break
        if source_executable:
            break
    if source_executable:
        invoke("dynamic_path_metrics", [
            sys.executable, str(V3 / "run_dynamic_path_metrics_v3.py"),
            "--manifest", str(final_manifest), "--executable", str(source_executable),
            "--output", str(qemu_path), "--work-root", str(args.work_root / instance / "qemu"),
            "--max-cases", str(args.qemu_max_cases), "--sampling", "uniform",
            "--case-timeout-cap", "15", "--skip-callgrind", "--discard-raw-traces",
        ], logs, env, 21600)
    if qemu_path.is_file():
        invoke("case_novelty_signals", [
            sys.executable, str(V3 / "build_case_novelty_signals_v3.py"),
            "--qemu-report", str(qemu_path), "--output", str(repo / "case_signals.json"),
        ], logs, env, 900)
    stop_path = repo / "stop_gate.json"
    invoke("stop_gate", [
        sys.executable, str(V3 / "evaluate_stop_v3.py"), "--coverage", str(coverage_json),
        "--quality", str(final_quality), "--profiles", str(ROOT / "v3" / "configs" / "coverage_profiles.json"),
        "--language", "go", "--output", str(stop_path),
    ], logs, env, 900)
    stop = json.loads(stop_path.read_text()) if stop_path.is_file() else {"status": "missing"}
    summary = {
        "schema": "programbench_oracle_gym_v3_repo_summary",
        "instance_id": instance, "suite_label": final_label, "cases_json": str(cases),
        "manifest": str(final_manifest), "coverage": str(coverage_json), "quality": str(final_quality),
        "generated_tests": coverage_data.get("generated_tests"),
        "native_tests": coverage_data.get("native_tests"),
        "coverage_timing": coverage_data.get("timing"),
        "binary_consistent": coverage_data.get("all_branch_binary_comparisons_consistent"),
        "quality_returncode": quality_code, "stop_gate": stop,
        "dynamic_path_metrics": json.loads(qemu_path.read_text()) if qemu_path.is_file() else None,
    }
    write(repo / "pipeline_summary.json", summary)
    write(status, {"state": "completed" if quality_code == 0 else "needs_repair", "stage": "done"})
    return 0 if quality_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
