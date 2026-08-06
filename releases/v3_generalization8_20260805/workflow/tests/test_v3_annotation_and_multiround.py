from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_script(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(path), *args], capture_output=True, text=True, check=True)


def test_annotation_priority_and_behavior_cap_keep_novel_representatives(tmp_path: Path) -> None:
    candidates = {"cases": [{"name": name, "args": [name], "stdin": ""} for name in ("a", "b", "c", "d")]}
    empty_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    captured = []
    for name in ("a", "b", "c", "d"):
        captured.append({
            "name": name, "args": [name], "stdin_sha256": empty_hash,
            "returncode": 0, "stdout_sha256": "same", "stderr_sha256": empty_hash,
            "stdout_bytes": 4, "stderr_bytes": 0, "timed_out": False,
        })
    manifest = {"cases": captured}
    signals = {"cases": {"c": {"new_source_blocks": 4}, "d": {"new_dynamic_edges": 3}}}
    candidate_path, manifest_path, signal_path = (tmp_path / name for name in ("candidates.json", "manifest.json", "signals.json"))
    candidate_path.write_text(json.dumps(candidates))
    manifest_path.write_text(json.dumps(manifest))
    signal_path.write_text(json.dumps(signals))
    annotations, queue = tmp_path / "annotations.json", tmp_path / "queue.csv"
    run_script(
        ROOT / "v3/tools/build_case_annotations_v3.py", "--capture-manifest", str(manifest_path),
        "--case-signals", str(signal_path), "--output-json", str(annotations), "--output-csv", str(queue),
    )
    output, report = tmp_path / "filtered.json", tmp_path / "report.json"
    run_script(
        ROOT / "v3/tools/filter_low_value_cases_v3.py", "--candidates", str(candidate_path),
        "--capture-manifest", str(manifest_path), "--annotations", str(annotations),
        "--behavior-cap", "2", "--output", str(output), "--report", str(report),
    )
    assert {case["name"] for case in json.loads(output.read_text())["cases"]} == {"c", "d"}
    with queue.open(encoding="utf-8-sig") as handle:
        assert len(list(csv.DictReader(handle))) == 4


def test_multiround_selector_accepts_target_and_stops_saturated_repo(tmp_path: Path) -> None:
    cohort = {"instances": [{"instance_id": "accepted", "language": "go"}, {"instance_id": "slow", "language": "c"}]}
    cohort_path = tmp_path / "cohort.json"
    cohort_path.write_text(json.dumps(cohort))
    history = tmp_path / "history.json"
    history.write_text(json.dumps({"instances": {"slow": [
        {"round": 0, "primary_percent": 70.0, "secondary_percent": 70.0, "quality_passed": False},
        {"round": 1, "primary_percent": 70.3, "secondary_percent": 70.2, "quality_passed": False},
    ]}}))
    prior = tmp_path / "prior"
    (prior / "accepted").mkdir(parents=True)
    (prior / "accepted" / "stop_gate.json").write_text(json.dumps({
        "status": "accepted_minimum", "primary_percent": 88.0, "secondary_percent": 86.0,
    }))
    (prior / "slow").mkdir(parents=True)
    (prior / "slow" / "stop_gate.json").write_text(json.dumps({
        "status": "refine", "primary_percent": 70.4, "secondary_percent": 70.3,
    }))
    active, summary = tmp_path / "active.json", tmp_path / "summary.json"
    run_script(
        ROOT / "v3/crosslang20/tools/select_refinement_cohort_v3.py",
        "--cohort", str(cohort_path), "--prior-root", str(prior), "--history", str(history),
        "--round", "3", "--output-cohort", str(active), "--output-summary", str(summary),
    )
    assert json.loads(active.read_text())["instances"] == []
    decisions = {row["instance_id"]: row["decision"] for row in json.loads(summary.read_text())["rows"]}
    assert decisions == {"accepted": "target_met", "slow": "saturated_below_target"}


def test_stop_gate_rejects_invalid_coverage_signal_even_with_high_numbers(tmp_path: Path) -> None:
    coverage = tmp_path / "coverage.json"
    quality = tmp_path / "quality.json"
    profiles = tmp_path / "profiles.json"
    output = tmp_path / "stop.json"
    coverage.write_text(json.dumps({
        "generated_tests": {
            "primary_coverage_percent": 99.0,
            "secondary_coverage_percent": 99.0,
            "coverage_signal_valid": False,
            "pytest_all_coverage_runs_passed": True,
        },
        "all_branch_binary_comparisons_consistent": True,
    }))
    quality.write_text(json.dumps({
        "all_dummies_rejected": True,
        "assertion_lint": {"passed": True},
        "source_leak_scan": {"passed": True},
    }))
    profiles.write_text(json.dumps({
        "c_cpp": {
            "minimum_primary_percent": 85.0, "stretch_primary_percent": 90.0,
            "minimum_secondary_percent": 85.0, "stretch_secondary_percent": 90.0,
        }
    }))
    run_script(
        ROOT / "v3/tools/evaluate_stop_v3.py", "--coverage", str(coverage),
        "--quality", str(quality), "--profiles", str(profiles), "--language", "c_cpp",
        "--output", str(output),
    )
    result = json.loads(output.read_text())
    assert result["status"] == "refine"
    assert result["checks"]["coverage_signal_valid"] is False
