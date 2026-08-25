from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from v4.programbench_v4.auditor import aggregate_audits, audit_campaign, main as auditor_main
import pytest

from v4.programbench_v4.controller import StageRunner, WorkflowError, run_campaign
from v4.programbench_v4.io import atomic_write_json, read_json, sha256_json
from v4.programbench_v4.provenance import source_tree_sha256
from v4.programbench_v4.state import AtomicState, CampaignLock


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "v4/tools/smoke_stage_adapter.py"


def config(tmp_path: Path, *, delay: float = 0.0, timeout: float = 5.0) -> tuple[Path, dict]:
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.go").write_text("package main\n", encoding="utf-8")
    value = {
        "schema": "programbench_v4_campaign_v1",
        "campaign_id": "fault-smoke",
        "mode": "smoke",
        "output_root": str(tmp_path / "output"),
        "repo_workers": 1,
        "generation_workers": 1,
        "heartbeat_seconds": 0.05,
        "stage_timeout_seconds": timeout,
        "stale_lock_seconds": 1,
        "marginal_policy": {
            "saturation_windows": 2,
            "minimum_promoted_observations": 3,
            "repo_wall_budget_seconds": 20,
        },
        "tranche_policy": {"initial_size": 8, "minimum_size": 4, "maximum_size": 16},
        "repositories": [
            {
                "instance_id": "repo",
                "language": "go",
                "commit": "a" * 40,
                "source_dir": str(source),
                "source_tree_sha256": source_tree_sha256(source),
                "runtime_image_id": "sha256:" + "b" * 64,
                "stage_adapter": [sys.executable, str(ADAPTER)],
                "scope_files": [str(ADAPTER)],
                "smoke_profile": {"delay_seconds": delay},
            }
        ],
    }
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, value


def test_second_controller_is_refused_by_campaign_lock(tmp_path: Path) -> None:
    path, value = config(tmp_path)
    output = Path(value["output_root"])
    lock = CampaignLock(
        output / "campaign.lock",
        campaign_id="fault-smoke",
        run_id="owner",
        config_sha256=sha256_json(value),
    ).acquire()
    try:
        assert run_campaign(path, run_id="contender") == 1
        status = read_json(output / "campaign_status.json")
        assert status["state"] == "needs_attention"
        assert "already owned" in status["error"]
    finally:
        lock.release()


def test_stage_timeout_is_terminal_and_does_not_claim_completion(tmp_path: Path) -> None:
    path, value = config(tmp_path, delay=1.0, timeout=0.2)
    assert run_campaign(path, run_id="timeout") == 1
    repo = read_json(Path(value["output_root"]) / "repositories/repo/status.json")
    assert repo["state"] == "needs_attention"
    assert "timed out" in repo["error"]


def test_auditor_writes_scoped_run_without_mutating_repo_control(tmp_path: Path) -> None:
    root = tmp_path / "campaign"
    repo = root / "repositories/repo"
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    atomic_write_json(
        repo / "status.json",
        {
            "schema": "programbench_v4_atomic_state_v1",
            "state": "running",
            "stage": "oracle_capture",
            "updated_at": old,
        },
    )
    atomic_write_json(root / "campaign_status.json", {"state": "running"})
    report = audit_campaign(
        root,
        stale_seconds=30,
        maximum_repo_files=1000,
        maximum_repo_bytes=1024 * 1024,
        audit_id="luna-stale-1",
        enforce_safe_pause=True,
    )
    assert any(issue["kind"] == "stale_heartbeat" for issue in report["issues"])
    assert report["audit_id"] == "luna-stale-1"
    assert report["intervention_mode"] == "advisory_only"
    assert (root / "audit/runs/luna-stale-1.json").is_file()
    assert not (root / "audit/latest.json").exists()
    assert not (repo / "control/request.json").exists()


def test_audit_aggregation_is_deterministic_and_atomic(tmp_path: Path) -> None:
    root = tmp_path / "campaign"
    atomic_write_json(root / "campaign_status.json", {"state": "running"})
    for audit_id in ("luna-b", "luna-a"):
        report = audit_campaign(
            root,
            stale_seconds=30,
            maximum_repo_files=1000,
            maximum_repo_bytes=1024 * 1024,
            audit_id=audit_id,
        )
        assert report["audit_id"] == audit_id
    first = aggregate_audits(root)
    first_bytes = (root / "audit/latest.json").read_bytes()
    second = aggregate_audits(root)
    second_bytes = (root / "audit/latest.json").read_bytes()
    assert first == second
    assert first_bytes == second_bytes
    assert first["audit_count"] == 2
    assert [row["audit_id"] for row in first["audits"]] == ["luna-a", "luna-b"]


def test_audit_id_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="path-safe"):
        audit_campaign(
            tmp_path / "campaign",
            stale_seconds=30,
            maximum_repo_files=1000,
            maximum_repo_bytes=1024 * 1024,
            audit_id="../escape",
        )


def test_auditor_cli_accepts_explicit_id_and_aggregate_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "campaign"
    monkeypatch.setattr(
        "v4.programbench_v4.auditor.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""),
    )
    assert auditor_main(["--campaign-root", str(root), "--audit-id", "cli-1"]) == 0
    assert (root / "audit/runs/cli-1.json").is_file()
    assert auditor_main(["--campaign-root", str(root), "--aggregate"]) == 0
    latest = read_json(root / "audit/latest.json")
    assert latest["audit_count"] == 1
    assert latest["audits"][0]["audit_id"] == "cli-1"


def test_pause_control_prevents_preflight_on_resume(tmp_path: Path) -> None:
    path, value = config(tmp_path)
    repo = Path(value["output_root"]) / "repositories/repo"
    atomic_write_json(
        repo / "control/request.json",
        {
            "schema": "programbench_v4_control_request_v1",
            "action": "pause_repo",
            "reason": "repeat_preflight_timeout",
        },
    )
    # A paused campaign is intentionally non-successful at the campaign level,
    # but it must not execute the repository's preflight stage.
    assert run_campaign(path, run_id="paused") == 1
    status = read_json(repo / "status.json")
    assert status["state"] == "paused"
    assert status["stage"] == "auditor_intervention"
    assert not (repo / "stages/iteration-0000/preflight/request.json").exists()


def test_auditor_ignores_queued_status_owned_by_an_old_run(tmp_path: Path) -> None:
    root = tmp_path / "campaign"
    repo = root / "repositories/repo"
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    atomic_write_json(
        repo / "status.json",
        {
            "schema": "programbench_v4_atomic_state_v1",
            "state": "running",
            "stage": "quality_gates",
            "run_id": "old-run",
            "updated_at": old,
        },
    )
    atomic_write_json(
        root / "campaign_status.json",
        {"state": "running", "run_id": "current-run"},
    )
    report = audit_campaign(
        root,
        stale_seconds=30,
        maximum_repo_files=1000,
        maximum_repo_bytes=1024 * 1024,
    )
    assert not any(issue["kind"] == "stale_heartbeat" for issue in report["issues"])


def test_atomic_json_remains_parseable_across_replacements(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    for sequence in range(50):
        atomic_write_json(path, {"sequence": sequence, "payload": "x" * 1000})
        assert read_json(path)["sequence"] == sequence
    assert not list(tmp_path.glob(".state.json.*.tmp"))


def test_invalid_old_scope_response_is_quarantined_before_failed_rerun(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    stage_root = repo_root / "stages/iteration-0001/plan_tranche"
    atomic_write_json(stage_root / "request.json", {"scope_sha256": "old"})
    atomic_write_json(
        stage_root / "response.json",
        {"scope_sha256": "old", "stage": "plan_tranche", "stale": True},
    )
    atomic_write_json(
        stage_root / "receipt.json",
        {"scope_sha256": "old", "request_sha256": "old", "exit_code": 0},
    )
    status = AtomicState(repo_root / "status.json", campaign_id="c", run_id="r")
    runner = StageRunner(
        adapter=[sys.executable, "-c", "import sys; sys.exit(7)"],
        repo_root=repo_root,
        scope_sha256="new",
        heartbeat_seconds=0.01,
        stage_timeout_seconds=2,
        status=status,
    )
    with pytest.raises(WorkflowError, match="response_exists=False"):
        runner.run(
            "plan_tranche",
            iteration=1,
            repo={},
            remaining_seconds=None,
        )
    assert not (stage_root / "response.json").exists()
    stale = list((stage_root / "stale").glob("*/response.json"))
    assert len(stale) == 1
    assert read_json(stale[0])["stale"] is True
