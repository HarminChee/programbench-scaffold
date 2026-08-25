from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from v4.programbench_v4.controller import (
    WorkflowError,
    _load_recovery_checkpoint,
    _write_recovery_checkpoint,
)
from v4.programbench_v4.io import atomic_write_json, read_json
from v4.programbench_v4.policy import MarginalPolicy, Observation


def _quality_report(path: Path) -> None:
    atomic_write_json(
        path,
        {
            "all_dummies_rejected": True,
            "all_target_executions_isolated": True,
            "all_tests_reject_all_dummies": True,
            "dummy_passing_test_names": [],
            "repeat_check": {"returncode": 0},
        },
    )


def test_zero_witness_expansion_rolls_back_but_counts_for_saturation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    repo_root = tmp_path / "repo"
    (repo_root / "artifacts").mkdir(parents=True)
    (repo_root / "candidates").mkdir()
    (repo_root / "quality").mkdir()
    bundle = repo_root / "bundle"
    (bundle / "eval").mkdir(parents=True)
    binary = tmp_path / "binary"
    binary.write_bytes(b"binary")
    accepted = [{"name": "accepted", "args": ["one"]}]
    expanded = [*accepted, {"name": "redundant", "args": ["two"]}]
    atomic_write_json(repo_root / "candidates/pre-iteration-0002.json", {"cases": accepted})
    atomic_write_json(repo_root / "candidates/current.json", {"cases": expanded})

    def captured(cases: list[dict]) -> list[dict]:
        return [
            {
                "name": case["name"],
                "returncode": 0,
                "stdout_bytes": 0,
                "stderr_bytes": 0,
            }
            for case in cases
        ]

    atomic_write_json(bundle / "eval/generated_cli_manifest.json", {"cases": captured(expanded)})
    atomic_write_json(
        repo_root / "artifacts/current_bundle.json", {"bundle_root": str(bundle)}
    )
    coverage = {
        "primary_coverage": 10.0,
        "total_statements": 10,
        "sample_policy": "complete_retained_suite",
        "covered_units": ["target.go:1"],
        "coverage_valid": True,
    }
    atomic_write_json(repo_root / "artifacts/latest_quick_coverage.json", coverage)
    _quality_report(repo_root / "quality/iteration-0002.json")
    atomic_write_json(
        repo_root / "artifacts/raw_candidate_ledger.json",
        {
            "iterations": {"2": 1},
            "unique_persisted_raw_candidates": 2,
        },
    )
    first_witnesses = adapter._case_static_witnesses(captured(accepted)[0], accepted[0])
    atomic_write_json(
        repo_root / "artifacts/witness_ledger.json",
        {
            "coverage_units": ["target.go:1"],
            "behaviors": sorted(
                value.removeprefix("behavior:")
                for value in first_witnesses
                if value.startswith("behavior:")
            ),
            "errors": [],
            "fixtures": [json.dumps(())],
            # Legacy ledgers omitted assertion/state dimensions. The adapter
            # must seed them from the pre-iteration accepted suite rather than
            # manufacture novelty during an upgrade.
        },
    )
    atomic_write_json(
        repo_root / "artifacts/preflight.json",
        {
            "coverage_binary_sha256": "a" * 64,
            "reference_binary": str(binary),
        },
    )
    scope = adapter._coverage_comparison_scope(
        coverage, coverage_binary_sha256="a" * 64, language="go"
    )

    def fake_capture(request: dict, root: Path) -> dict:
        current = read_json(root / "candidates/current.json")["cases"]
        atomic_write_json(bundle / "eval/generated_cli_manifest.json", {"cases": captured(current)})
        atomic_write_json(root / "artifacts/current_bundle.json", {"bundle_root": str(bundle)})
        return {"captured_cases": len(current)}

    def fake_coverage(request: dict, root: Path, *, full: bool) -> dict:
        assert not full
        atomic_write_json(root / "artifacts/latest_quick_coverage.json", coverage)
        return {"execution_provenance": [{"stage": "quick_coverage"}]}

    def fake_quality(request: dict, root: Path) -> dict:
        path = root / "quality/iteration-0002.json"
        _quality_report(path)
        return {"quality_report": str(path)}

    monkeypatch.setattr(adapter, "capture", fake_capture)
    monkeypatch.setattr(adapter, "_coverage_profile", fake_coverage)
    monkeypatch.setattr(adapter, "quality", fake_quality)
    request = {
        "iteration": 2,
        "stage": "evaluate_marginal",
        "repository": {"language": "go", "runtime_image_id": "sha256:" + "b" * 64},
        "workflow_context": {
            "marginal_history": [
                {
                    "primary_coverage": 10.0,
                    "coverage_scope_sha256": scope,
                    "retained_cases": 1,
                    "generated_candidates": 1,
                    "wall_seconds": 1.0,
                    "new_behavior_families": 1,
                    "covered_units": ["target.go:1"],
                    "promoted": True,
                    "quality_passed": True,
                    "coverage_valid": True,
                }
            ]
        },
    }
    response = adapter.evaluate(request, repo_root)
    observation = Observation(**response["observation"])
    assert observation.promoted
    assert observation.novelty_total() == 0
    assert observation.retained_cases == 1
    assert read_json(repo_root / "candidates/current.json")["cases"] == accepted
    rollback = response["zero_witness_rollback"]
    assert rollback["rejected_candidate_count"] == 2
    assert rollback["restored_candidate_count"] == 1
    assert response["accepted_artifact_bindings"]["candidate_state_sha256"]

    baseline = Observation(
        primary_coverage=10.0,
        coverage_scope_sha256=scope,
        retained_cases=1,
        generated_candidates=1,
        wall_seconds=1.0,
        new_behavior_families=1,
        covered_units=("target.go:1",),
    )
    second_zero = replace(observation)
    decision = MarginalPolicy(
        saturation_windows=2,
        minimum_promoted_observations=3,
        minimum_primary_gain_pp=0.25,
        minimum_novelty=1,
    ).decide(
        [baseline, observation, second_zero],
        total_raw_candidates=3,
        retained_suite_cases=1,
        iteration=3,
    )
    assert decision.reason == "marginal_saturation"
    assert decision.successful

    repo = {
        "instance_id": "air",
        "commit": "c" * 40,
        "source_tree_sha256": "d" * 64,
        "runtime_image_id": "sha256:" + "b" * 64,
    }
    _write_recovery_checkpoint(
        repo_root=repo_root,
        repo=repo,
        scope_sha256="e" * 64,
        observations=[baseline, observation],
        total_raw=2,
        retained=1,
        iteration=2,
        accepted_artifact_bindings=response["accepted_artifact_bindings"],
    )
    loaded = _load_recovery_checkpoint(repo_root, repo)
    assert loaded[2] == 1
    restored_quality = (
        repo_root / "zero_witness_rollbacks/iteration-0002/restored_quality.json"
    )
    restored_quality.write_text("{}", encoding="utf-8")
    with pytest.raises(WorkflowError, match="snapshot digest changed"):
        _load_recovery_checkpoint(repo_root, repo)
