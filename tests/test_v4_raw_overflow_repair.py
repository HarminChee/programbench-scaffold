from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from v4.programbench_v4.candidates import exact_key
from v4.programbench_v4.controller import _repository_recovery_identity
from v4.programbench_v4.io import atomic_write_json, read_json
from v4.programbench_v4.raw_overflow_repair import (
    RawOverflowRepairError,
    apply_raw_overflow_repair,
    plan_raw_overflow_repair,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repair_fixture(tmp_path: Path, *, accepted_last: bool = False) -> tuple[dict, Path, str]:
    instance = "mvdan__sh.abc1234"
    root = tmp_path / "campaign"
    repo_root = root / "repositories" / instance
    (repo_root / "agent_cases").mkdir(parents=True)
    (repo_root / "artifacts").mkdir()
    (repo_root / "candidates").mkdir()
    source = tmp_path / "source"
    source.mkdir()
    repo = {
        "instance_id": instance,
        "language": "go",
        "commit": "a" * 40,
        "source_dir": str(source),
        "source_tree_sha256": "b" * 64,
        "runtime_image_id": "sha256:" + "c" * 64,
    }
    config = {
        "marginal_policy": {
            "raw_candidate_fuse": 2,
            "adaptive_raw_fuse": True,
            "raw_candidate_hard_fuse": 3,
        },
        "repositories": [repo],
    }
    cases = [{"name": f"case-{index}", "args": [str(index)]} for index in range(5)]
    tranche = repo_root / "agent_cases/tranche-0031.json"
    atomic_write_json(tranche, {"cases": cases})
    accepted = [cases[-1]] if accepted_last else cases[:2]
    candidate = repo_root / "candidates/current.json"
    atomic_write_json(candidate, {"cases": accepted})
    keys = sorted(exact_key(case) for case in cases)
    atomic_write_json(
        repo_root / "artifacts/raw_candidate_ledger.json",
        {
            "schema": "programbench_v4_raw_candidate_ledger_v2",
            "iterations": {"31": 5},
            "total_generation_attempts": 5,
            "unique_candidate_keys": keys,
            "unique_persisted_raw_candidates": 5,
            "total_generated_candidates": 5,
        },
    )
    atomic_write_json(
        repo_root / "recovery_checkpoint.json",
        {
            "schema": "programbench_v4_recovery_checkpoint_v1",
            "repository_recovery_identity": _repository_recovery_identity(repo),
            "scope_sha256": "d" * 64,
            "candidate_state_sha256": sha(candidate),
            "last_completed_iteration": 30,
            "unique_persisted_raw_candidates": 2,
            "retained_suite_cases": len(accepted),
            "observations": [],
        },
    )
    return config, root, instance


def test_raw_overflow_repair_is_dry_run_auditable_and_preserves_generation(tmp_path: Path) -> None:
    config, root, instance = repair_fixture(tmp_path)
    repo_root = root / "repositories" / instance
    tranche = repo_root / "agent_cases/tranche-0031.json"
    tranche_before = sha(tranche)
    ledger_before = sha(repo_root / "artifacts/raw_candidate_ledger.json")

    plan = plan_raw_overflow_repair(
        campaign_config=config, campaign_root=root, instance_id=instance
    )
    assert plan["dry_run"]
    assert plan["ledger_count_before"] == 5
    assert plan["ledger_count_after"] == 3
    assert plan["overflow_unique_candidates"] == 2
    assert sha(repo_root / "artifacts/raw_candidate_ledger.json") == ledger_before

    applied = apply_raw_overflow_repair(plan, campaign_root=root)
    assert not applied["dry_run"]
    assert sha(tranche) == tranche_before
    ledger = read_json(repo_root / "artifacts/raw_candidate_ledger.json")
    assert ledger["unique_persisted_raw_candidates"] == 3
    assert len(ledger["unique_candidate_keys"]) == 3
    assert ledger["total_generation_attempts"] == 5
    checkpoint = read_json(repo_root / "recovery_checkpoint.json")
    assert checkpoint["unique_persisted_raw_candidates"] == 3
    overflow = read_json(repo_root / "artifacts/raw_candidate_overflow/legacy-repair.json")
    assert overflow["overflow_unique_candidates"] == 2
    assert len(overflow["cases"]) == 2
    assert (repo_root / "legacy_recovery/raw_hard_fuse_overflow/repair_report.json").is_file()
    with pytest.raises(RawOverflowRepairError, match="one-time"):
        apply_raw_overflow_repair(plan, campaign_root=root)


def test_raw_overflow_repair_fails_if_accepted_witness_would_be_quarantined(tmp_path: Path) -> None:
    config, root, instance = repair_fixture(tmp_path, accepted_last=True)
    with pytest.raises(RawOverflowRepairError, match="accepted-suite witness"):
        plan_raw_overflow_repair(
            campaign_config=config, campaign_root=root, instance_id=instance
        )


def test_raw_overflow_repair_conservatively_admits_opaque_legacy_keys(tmp_path: Path) -> None:
    config, root, instance = repair_fixture(tmp_path)
    repo_root = root / "repositories" / instance
    ledger_path = repo_root / "artifacts/raw_candidate_ledger.json"
    ledger = read_json(ledger_path)
    opaque = "f" * 64
    ledger["unique_candidate_keys"].append(opaque)
    ledger["unique_candidate_keys"].sort()
    ledger["unique_persisted_raw_candidates"] = 6
    ledger["total_generated_candidates"] = 6
    atomic_write_json(ledger_path, ledger)

    plan = plan_raw_overflow_repair(
        campaign_config=config, campaign_root=root, instance_id=instance
    )
    assert plan["legacy_opaque_candidate_keys"] == [opaque]
    assert opaque in plan["admitted_unique_candidate_keys"]
    assert plan["ledger_count_after"] == 3
    assert plan["overflow_unique_candidates"] == 3


def test_raw_overflow_repair_rejects_changed_generation_evidence(tmp_path: Path) -> None:
    config, root, instance = repair_fixture(tmp_path)
    plan = plan_raw_overflow_repair(
        campaign_config=config, campaign_root=root, instance_id=instance
    )
    tranche = root / "repositories" / instance / "agent_cases/tranche-0031.json"
    tranche.write_text(json.dumps({"cases": []}), encoding="utf-8")
    with pytest.raises(RawOverflowRepairError, match="generation artifact changed"):
        apply_raw_overflow_repair(plan, campaign_root=root)
