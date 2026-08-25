from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from v4.programbench_v4.io import atomic_write_json, read_json
from v4.programbench_v4.legacy_recovery import (
    LegacyRecoveryError,
    apply_legacy_recovery,
    plan_legacy_recovery,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_stage(root: Path, iteration: int, stage: str, payload: dict) -> None:
    stage_root = root / f"stages/iteration-{iteration:04d}/{stage}"
    response = {"stage": stage, "scope_sha256": "old-scope", **payload}
    atomic_write_json(stage_root / "request.json", {"scope_sha256": "old-scope"})
    atomic_write_json(stage_root / "response.json", response)
    atomic_write_json(
        stage_root / "receipt.json",
        {
            "scope_sha256": "old-scope",
            "response_sha256": sha(stage_root / "response.json"),
            "exit_code": 0,
            "timed_out": False,
        },
    )


def fixture(tmp_path: Path, coverages: list[float], *, raw_fuse: bool = False) -> tuple[dict, Path, str]:
    campaign_root = tmp_path / "output"
    instance = "owner__repo.abc1234"
    repo_root = campaign_root / "repositories" / instance
    (repo_root / "artifacts").mkdir(parents=True)
    (repo_root / "candidates").mkdir()
    (repo_root / "agent_cases").mkdir()
    binary = tmp_path / "coverage-binary"
    binary.write_bytes(b"binary")
    atomic_write_json(
        repo_root / "artifacts/preflight.json",
        {"coverage_binary": str(binary), "coverage_binary_sha256": sha(binary)},
    )
    source = tmp_path / "source"
    source.mkdir()
    config = {
        "repositories": [
            {
                "instance_id": instance,
                "language": "go",
                "commit": "a" * 40,
                "source_dir": str(source),
                "source_tree_sha256": "b" * 64,
                "runtime_image_id": "sha256:" + "c" * 64,
            }
        ]
    }
    for iteration, coverage in enumerate(coverages, 1):
        cases = [{"name": f"accepted-{index}", "args": [str(index)]} for index in range(iteration)]
        atomic_write_json(repo_root / f"agent_cases/tranche-{iteration:04d}.json", {"cases": cases})
        quality_path = repo_root / f"quality/iteration-{iteration:04d}.json"
        atomic_write_json(
            quality_path,
            {
                "all_dummies_rejected": True,
                "all_target_executions_isolated": True,
                "all_tests_reject_all_dummies": True,
                "dummy_passing_test_names": [],
                "python": {"returncode": 0},
                "repeat_check": {"returncode": 0},
            },
        )
        observation = {
            "primary_coverage": coverage,
            "coverage_scope_sha256": "legacy",
            "retained_cases": iteration,
            "generated_candidates": iteration,
            "wall_seconds": 1,
            "new_behavior_families": 1,
        }
        write_stage(repo_root, iteration, "oracle_capture", {"captured_cases": iteration})
        write_stage(
            repo_root,
            iteration,
            "quick_coverage",
            {
                "coverage": {
                    "primary_coverage": coverage,
                    "total_statements": 100,
                    "sample_policy": "complete_retained_suite",
                }
            },
        )
        atomic_write_json(
            repo_root / f"coverage/quick-{iteration:04d}/coverage.json",
            {
                "primary_coverage": coverage,
                "total_statements": 100,
                "sample_policy": "complete_retained_suite",
            },
        )
        write_stage(
            repo_root,
            iteration,
            "quality_gates",
            {
                "quality_report": str(quality_path),
                "witness_replacement": {
                    "selected_case_ids": [case["name"] for case in cases]
                },
            },
        )
        write_stage(
            repo_root,
            iteration,
            "evaluate_marginal",
            {"observation": observation, "retained_suite_cases": iteration},
        )
        if iteration < len(coverages):
            atomic_write_json(
                repo_root / f"candidates/pre-iteration-{iteration + 1:04d}.json",
                {"cases": cases},
            )
    accepted_count = len(coverages) if raw_fuse else len(coverages) - 1
    accepted = [
        {"name": f"accepted-{index}", "args": [str(index)]}
        for index in range(accepted_count)
    ]
    if raw_fuse:
        staged = [*accepted, {"name": "staged", "args": ["staged"]}]
        atomic_write_json(repo_root / "candidates/current.json", {"cases": staged})
        atomic_write_json(
            repo_root / f"candidates/pre-iteration-{len(coverages) + 1:04d}.json",
            {"cases": accepted},
        )
        atomic_write_json(
            repo_root / "agent_cases" / f"tranche-{len(coverages) + 1:04d}.json",
            {"cases": [{"name": "staged", "args": ["staged"]}]},
        )
        atomic_write_json(
            repo_root / "status.json",
            {
                "iteration": len(coverages) + 1,
                "stop_reason": "raw_candidate_fuse_before_capture",
            },
        )
    else:
        bad = [{"name": f"accepted-{index}", "args": [str(index)]} for index in range(len(coverages))]
        atomic_write_json(repo_root / "candidates/current.json", {"cases": bad})
        atomic_write_json(repo_root / "status.json", {"iteration": len(coverages)})
    return config, campaign_root, instance


def test_migration_rolls_back_first_nonmonotonic_tranche_and_is_one_time(tmp_path: Path) -> None:
    config, campaign_root, instance = fixture(tmp_path, [10.0, 9.0])
    repo_root = campaign_root / "repositories" / instance
    before = sha(repo_root / "candidates/current.json")
    plan = plan_legacy_recovery(
        campaign_config=config, campaign_root=campaign_root, instance_id=instance
    )
    assert plan["dry_run"]
    assert plan["action"] == "rollback_coverage_regression"
    assert plan["accepted_iterations"] == [1]
    assert plan["rejected_iteration"] == 2
    assert sha(repo_root / "candidates/current.json") == before

    applied = apply_legacy_recovery(plan, campaign_root=campaign_root)
    assert not applied["dry_run"]
    assert len(read_json(repo_root / "candidates/current.json")["cases"]) == 1
    assert read_json(repo_root / "recovery_checkpoint.json")["last_completed_iteration"] == 1
    assert (repo_root / "legacy_recovery/rollback_coverage_regression/current.before.json").is_file()
    with pytest.raises(LegacyRecoveryError, match="one-time"):
        apply_legacy_recovery(plan, campaign_root=campaign_root)


def test_migration_rolls_back_uncaptured_raw_fuse_staging(tmp_path: Path) -> None:
    config, campaign_root, instance = fixture(tmp_path, [10.0], raw_fuse=True)
    plan = plan_legacy_recovery(
        campaign_config=config, campaign_root=campaign_root, instance_id=instance
    )
    assert plan["action"] == "rollback_staged_raw_fuse"
    assert plan["stopped_iteration"] == 2
    assert plan["raw_generation_attempts"] == 2
    apply_legacy_recovery(plan, campaign_root=campaign_root)
    repo_root = campaign_root / "repositories" / instance
    assert len(read_json(repo_root / "candidates/current.json")["cases"]) == 1
    ledger = read_json(repo_root / "artifacts/raw_candidate_ledger.json")
    assert ledger["unique_persisted_raw_candidates"] == 2
