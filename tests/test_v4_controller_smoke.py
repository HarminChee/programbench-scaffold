from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from v4.programbench_v4.controller import (
    FairGenerationSemaphore,
    WorkflowError,
    _load_recovery_checkpoint,
    _repository_recovery_identity,
    _run_iteration_stage,
    run_campaign,
    validate_config,
)
from v4.programbench_v4.io import read_json
from v4.programbench_v4.provenance import source_tree_sha256


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "v4/tools/smoke_stage_adapter.py"


def campaign(tmp_path: Path, *, repos: int = 2) -> tuple[Path, dict]:
    repositories = []
    for index in range(repos):
        source = tmp_path / f"source-{index}"
        source.mkdir()
        (source / "README.md").write_text(f"fixture {index}\n", encoding="utf-8")
        repositories.append(
            {
                "instance_id": f"repo-{index}",
                "language": "go" if index % 2 == 0 else "rust",
                "commit": f"{index + 1:x}" * 40,
                "source_dir": str(source),
                "source_tree_sha256": source_tree_sha256(source),
                "runtime_image_id": "sha256:" + f"{index + 1:x}" * 64,
                "stage_adapter": [sys.executable, str(ADAPTER)],
                "scope_files": [str(ADAPTER)],
                "smoke_profile": {"delay_seconds": 0.01},
            }
        )
    value = {
        "schema": "programbench_v4_campaign_v1",
        "campaign_id": "smoke",
        "mode": "smoke",
        "output_root": str(tmp_path / "output"),
        "repo_workers": 2,
        "generation_workers": 2,
        "heartbeat_seconds": 0.05,
        "stage_timeout_seconds": 10,
        "stale_lock_seconds": 5,
        "marginal_policy": {
            "saturation_windows": 2,
            "minimum_promoted_observations": 3,
            "minimum_primary_gain_pp": 0.35,
            "minimum_novelty": 1,
            "repo_wall_budget_seconds": 30,
            "raw_candidate_fuse": 1000,
            "retained_suite_fuse": 100,
        },
        "tranche_policy": {"initial_size": 16, "minimum_size": 8, "maximum_size": 32},
        "repositories": repositories,
    }
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    return path, value


def test_parallel_controller_reaches_marginal_freeze(tmp_path: Path) -> None:
    path, value = campaign(tmp_path)
    assert run_campaign(path, run_id="smoke-run") == 0
    status = read_json(Path(value["output_root"]) / "campaign_status.json")
    assert status["state"] == "completed"
    assert len(status["results"]) == 2
    for repo in value["repositories"]:
        repo_status = read_json(
            Path(value["output_root"]) / "repositories" / repo["instance_id"] / "status.json"
        )
        assert repo_status["state"] == "completed"
        assert repo_status["stop_reason"] == "marginal_saturation"
        assert repo_status["iteration"] == 3


def test_generation_concurrency_is_limited_without_serializing_other_stages() -> None:
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    class Status:
        def write(self, **values: object) -> None:
            return None

    class Runner:
        heartbeat_seconds = 0.01
        status = Status()

        def run(self, stage: str, **values: object) -> dict:
            nonlocal active, maximum_active
            if stage != "generate":
                return {"stage": stage}
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(0.04)
                return {"stage": stage}
            finally:
                with lock:
                    active -= 1

    runner = Runner()
    semaphore = threading.BoundedSemaphore(2)
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(
                _run_iteration_stage,
                runner,
                stage="generate",
                iteration=1,
                repo={},
                remaining_seconds=5,
                workflow_context={},
                generation_semaphore=semaphore,
            )
            for _ in range(6)
        ]
        assert [future.result()["stage"] for future in futures] == ["generate"] * 6
    assert maximum_active == 2

    semaphore.acquire()
    semaphore.acquire()
    try:
        assert _run_iteration_stage(
            runner,
            stage="quick_coverage",
            iteration=1,
            repo={},
            remaining_seconds=1,
            workflow_context={},
            generation_semaphore=semaphore,
        ) == {"stage": "quick_coverage"}
    finally:
        semaphore.release()
        semaphore.release()


def test_generation_workers_default_and_bounds(tmp_path: Path) -> None:
    _, value = campaign(tmp_path)
    value["repo_workers"] = 10
    value["generation_workers"] = 8
    validate_config(value)
    value["generation_workers"] = 9
    with pytest.raises(WorkflowError, match="generation_workers"):
        validate_config(value)
    value.pop("generation_workers")
    validate_config(value)
    value["repo_workers"] = 2
    value["generation_workers"] = 3
    with pytest.raises(WorkflowError, match="generation_workers"):
        validate_config(value)


def test_fair_generation_gate_allows_eight_slots_and_no_waiter_starvation() -> None:
    gate = FairGenerationSemaphore(8)
    acquired = [gate.acquire(timeout=0.1) for _ in range(8)]
    assert acquired == [True] * 8
    result: list[int] = []

    def waiter(index: int) -> None:
        assert gate.acquire(timeout=1.0)
        result.append(index)
        time.sleep(0.005)
        gate.release()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(waiter, index) for index in range(4)]
        time.sleep(0.02)
        for _ in range(8):
            gate.release()
        for future in futures:
            future.result()
    assert sorted(result) == [0, 1, 2, 3]


def test_fast_settlement_revalidates_full_suite_above_native(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    assert run_campaign(path, run_id="baseline-run") == 0
    repo_root = Path(value["output_root"]) / "repositories/repo-0"
    checkpoint = read_json(repo_root / "recovery_checkpoint.json")
    accepted_iteration = checkpoint["last_completed_iteration"]
    primary = float(checkpoint["observations"][-1]["primary_coverage"])

    value["fast_settlement"] = {
        "repositories": [
            {
                "instance_id": "repo-0",
                "native_primary_coverage": primary - 1.0,
                "minimum_margin_pp": 0.0,
            }
        ]
    }
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    assert run_campaign(path, run_id="fast-settle-run") == 0
    status = read_json(repo_root / "status.json")
    summary = read_json(repo_root / "frozen/settlement_summary.json")
    assert status["state"] == "completed"
    assert status["stop_reason"] == "validated_checkpoint_above_native"
    assert status["iteration"] == accepted_iteration
    assert summary["final_primary_coverage"] > summary["native_primary_coverage"]
    assert summary["retained_suite_cases"] == summary["retained_checkpoint_cases"]
    assert set(summary["final_verification"]) == {
        "final_capture",
        "full_coverage",
        "freeze_verification",
        "freeze",
    }


def test_fast_settlement_config_rejects_unknown_repository(tmp_path: Path) -> None:
    _, value = campaign(tmp_path, repos=1)
    value["fast_settlement"] = {
        "repositories": [
            {"instance_id": "missing", "native_primary_coverage": 1.0}
        ]
    }
    with pytest.raises(WorkflowError, match="unknown repository"):
        validate_config(value)


def test_operator_can_settle_below_native_without_misreporting(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    assert run_campaign(path, run_id="baseline-run") == 0
    repo_root = Path(value["output_root"]) / "repositories/repo-0"
    checkpoint = read_json(repo_root / "recovery_checkpoint.json")
    primary = float(checkpoint["observations"][-1]["primary_coverage"])
    value["fast_settlement"] = {
        "repositories": [
            {
                "instance_id": "repo-0",
                "native_primary_coverage": primary + 10.0,
                "minimum_margin_pp": 0.0,
                "allow_below_native_manual": True,
            }
        ]
    }
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    assert run_campaign(path, run_id="manual-settle-run") == 0
    status = read_json(repo_root / "status.json")
    summary = read_json(repo_root / "frozen/settlement_summary.json")
    assert status["state"] == "completed"
    assert status["stop_reason"] == "operator_approved_settlement"
    assert summary["operator_approved"] is True
    assert summary["coverage_above_native"] is False
    assert summary["final_primary_coverage"] < summary["native_primary_coverage"]


def test_resume_reuses_scoped_stage_receipts(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    assert run_campaign(path, run_id="same-run") == 0
    response = (
        Path(value["output_root"])
        / "repositories/repo-0/stages/iteration-0001/generate/response.json"
    )
    before = response.stat().st_mtime_ns
    assert run_campaign(path, run_id="same-run") == 0
    assert response.stat().st_mtime_ns == before
    repo_status = read_json(
        Path(value["output_root"]) / "repositories/repo-0/status.json"
    )
    assert repo_status["iteration"] == 3


def test_recovery_checkpoint_imports_only_exact_candidate_state(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    assert run_campaign(path, run_id="checkpoint-run") == 0
    repo_root = Path(value["output_root"]) / "repositories/repo-0"
    checkpoint = read_json(repo_root / "recovery_checkpoint.json")
    assert checkpoint["last_completed_iteration"] == 3
    assert len(checkpoint["observations"]) == 3

    candidates = read_json(repo_root / "candidates/current.json")
    candidates["cases"].append({"name": "out-of-band", "args": []})
    (repo_root / "candidates/current.json").write_text(
        json.dumps(candidates), encoding="utf-8"
    )
    # A controller code/scope change would enter checkpoint import; corrupt or
    # manually edited suite state must fail closed instead of being resumed.
    value["repositories"][0]["scope_files"].append(str(path))
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    assert run_campaign(path, run_id="changed-scope-run") == 1
    status = read_json(repo_root / "status.json")
    assert "candidate state changed" in status["error"]


def test_recovery_checkpoint_finds_newest_exact_backup_after_dropped_iterations(
    tmp_path: Path,
) -> None:
    import hashlib

    repo_root = tmp_path / "repo"
    candidates = repo_root / "candidates"
    candidates.mkdir(parents=True)
    accepted = b'{"cases":[{"name":"accepted"}]}\n'
    (candidates / "current.json").write_bytes(b'{"cases":[{"name":"interrupted"}]}\n')
    (candidates / "pre-iteration-0004.json").write_bytes(b'{"cases":[]}\n')
    (candidates / "pre-iteration-0006.json").write_bytes(accepted)
    repo = {
        "instance_id": "repo",
        "commit": "a" * 40,
        "source_tree_sha256": "b" * 64,
        "runtime_image_id": "sha256:" + "c" * 64,
    }
    (repo_root / "recovery_checkpoint.json").write_text(
        json.dumps(
            {
                "schema": "programbench_v4_recovery_checkpoint_v1",
                "repository_recovery_identity": _repository_recovery_identity(repo),
                "candidate_state_sha256": hashlib.sha256(accepted).hexdigest(),
                "last_completed_iteration": 3,
                "unique_persisted_raw_candidates": 10,
                "retained_suite_cases": 1,
                "observations": [],
                "scope_sha256": "old-scope",
            }
        ),
        encoding="utf-8",
    )

    _load_recovery_checkpoint(repo_root, repo)
    assert (candidates / "current.json").read_bytes() == accepted
    audit = read_json(repo_root / "recovery_autorollback.json")
    assert audit["restored_from"].endswith("pre-iteration-0006.json")


def test_recovered_full_raw_ledger_pauses_before_generating_again(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    assert run_campaign(path, run_id="raw-full-first") == 0
    repo_root = Path(value["output_root"]) / "repositories/repo-0"
    checkpoint_path = repo_root / "recovery_checkpoint.json"
    checkpoint = read_json(checkpoint_path)
    checkpoint["unique_persisted_raw_candidates"] = 12
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")
    value["marginal_policy"]["raw_candidate_fuse"] = 12
    # Force a new execution scope while preserving immutable target identity.
    value["repositories"][0]["scope_files"].append(str(path))
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    assert run_campaign(path, run_id="raw-full-resume") == 1
    status = read_json(repo_root / "status.json")
    assert status["state"] == "paused"
    assert status["stop_reason"] == "raw_candidate_fuse_exhausted"
    assert status["iteration"] == 3
    assert not (repo_root / "stages/iteration-0004/generate").exists()


def test_production_forbids_pilot_round_fuse(tmp_path: Path) -> None:
    _, value = campaign(tmp_path, repos=1)
    value["mode"] = "production"
    value["marginal_policy"]["pilot_iteration_fuse"] = 2
    with pytest.raises(WorkflowError, match="forbids"):
        validate_config(value)


def test_fixed_coverage_and_round_fields_are_rejected(tmp_path: Path) -> None:
    _, value = campaign(tmp_path, repos=1)
    value["coverage_target"] = 90
    with pytest.raises(WorkflowError, match="forbids"):
        validate_config(value)


def test_retained_fuse_stops_before_expensive_capture(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    value["marginal_policy"]["retained_suite_fuse"] = 20
    value["repositories"][0]["smoke_profile"]["projected_cumulative_cases"] = 21
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    assert run_campaign(path, run_id="fuse-run") == 1
    repo_root = Path(value["output_root"]) / "repositories/repo-0"
    status = read_json(repo_root / "status.json")
    assert status["state"] == "needs_attention"
    assert status["stop_reason"] == "retained_suite_fuse_before_capture"
    assert not (repo_root / "stages/iteration-0001/oracle_capture").exists()


def test_raw_hard_fuse_overflow_pauses_without_capture_or_suite_mutation(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    value["marginal_policy"]["raw_candidate_fuse"] = 12
    value["repositories"][0]["smoke_profile"]["raw_overflow_count"] = 4
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    assert run_campaign(path, run_id="raw-overflow-run") == 1
    repo_root = Path(value["output_root"]) / "repositories/repo-0"
    status = read_json(repo_root / "status.json")
    assert status["state"] == "paused"
    assert status["stage"] == "paused_incomplete"
    assert status["stop_reason"] == "raw_candidate_hard_fuse_overflow_before_capture"
    assert status["evidence"]["raw_overflow_count"] == 4
    assert not (repo_root / "candidates/current.json").exists()
    assert not (repo_root / "stages/iteration-0001/oracle_capture").exists()


def test_quality_block_does_not_enter_marginal_history(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    value["repositories"][0]["smoke_profile"]["quality_unrepairable"] = True
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    assert run_campaign(path, run_id="quality-block-run") == 1
    repo_root = Path(value["output_root"]) / "repositories/repo-0"
    status = read_json(repo_root / "status.json")
    assert status["stage"] == "quality_blocked"
    assert status["stop_reason"] == "quality_blocked"
    assert not (repo_root / "stages/iteration-0001/evaluate_marginal").exists()


def test_generate_transport_failure_drops_only_failed_tranche(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    value["repositories"][0]["smoke_profile"].update(
        {"fail_stage_once": "generate", "fail_stage_iteration": 2}
    )
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    assert run_campaign(path, run_id="generate-recovery-run") == 0
    repo_root = Path(value["output_root"]) / "repositories/repo-0"
    status = read_json(repo_root / "status.json")
    assert status["state"] == "completed"
    assert status["iteration"] == 4
    dropped = read_json(repo_root / "dropped_tranches/iteration-0002-generate.json")
    assert dropped["failed_stage"] == "generate"
    assert "exit=7" in dropped["error"]
    assert not (repo_root / "stages/iteration-0002/oracle_capture").exists()


def test_declared_replacement_may_stage_above_retained_fuse(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    value["marginal_policy"]["retained_suite_fuse"] = 20
    profile = value["repositories"][0]["smoke_profile"]
    profile["projected_cumulative_cases"] = 21
    profile["replacement_planned"] = True
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    assert run_campaign(path, run_id="replacement-run") == 0
    repo_root = Path(value["output_root"]) / "repositories/repo-0"
    assert (repo_root / "stages/iteration-0001/oracle_capture/response.json").is_file()


def test_adaptive_replacement_cannot_cross_hard_fuse_before_capture(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    value["marginal_policy"].update(
        {
            "retained_suite_fuse": 20,
            "adaptive_retained_fuse": True,
            "retained_suite_hard_fuse": 30,
        }
    )
    profile = value["repositories"][0]["smoke_profile"]
    profile["projected_cumulative_cases"] = 31
    profile["replacement_planned"] = True
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    assert run_campaign(path, run_id="adaptive-hard-fuse-run") == 1
    repo_root = Path(value["output_root"]) / "repositories/repo-0"
    status = read_json(repo_root / "status.json")
    assert status["stop_reason"] == "retained_suite_fuse_before_capture"
    assert not (repo_root / "stages/iteration-0001/oracle_capture").exists()


def test_suite_witness_capacity_pause_finalizes_rolled_back_suite(tmp_path: Path) -> None:
    path, value = campaign(tmp_path, repos=1)
    value["repositories"][0]["smoke_profile"]["suite_fuse_blocked"] = True
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    assert run_campaign(path, run_id="suite-capacity-run") == 1
    repo_root = Path(value["output_root"]) / "repositories/repo-0"
    status = read_json(repo_root / "status.json")
    assert status["state"] == "paused"
    assert status["stop_reason"] == "suite_witness_capacity_exhausted"
    assert status["retained_suite_cases"] == 17
    assert (repo_root / "stages/iteration-0001/full_coverage/response.json").is_file()
    assert (repo_root / "stages/iteration-0001/freeze_verification/response.json").is_file()
    assert not (repo_root / "stages/iteration-0001/freeze").exists()
    paused = read_json(repo_root / "paused_suite_summary.json")
    assert paused["successful"] is False
    assert paused["stop_reason"] == "suite_witness_capacity_exhausted"
    assert not (repo_root / "stages/iteration-0001/evaluate_marginal").exists()
