#!/usr/bin/env python3
"""Reverify a transactionally rolled-back V4 suite after a safety pause.

This is not an accepting freeze path.  It publishes a scoped, fully verified
``paused_suite_summary.json`` while retaining ``successful=false``.
"""

from __future__ import annotations

import argparse
import hashlib
import uuid
from pathlib import Path

from v4.programbench_v4.controller import (
    StageRunner,
    _validate_stage_response,
    campaign_scope,
    validate_config,
)
from v4.programbench_v4.io import atomic_write_json, read_json, sha256_json
from v4.programbench_v4.provenance import validate_repository_scope
from v4.programbench_v4.state import AtomicState, CampaignLock, utc_now


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--iteration", type=int, required=True)
    args = parser.parse_args()
    config = read_json(args.config.resolve(strict=True))
    validate_config(config)
    repo_root = args.repo_root.resolve(strict=True)
    matches = [
        repo for repo in config["repositories"] if repo["instance_id"] == repo_root.name
    ]
    if len(matches) != 1:
        raise RuntimeError("repo root does not identify exactly one configured repository")
    repo = matches[0]
    validate_repository_scope(repo)
    candidates = repo_root / "candidates/current.json"
    cases = read_json(candidates).get("cases") or []
    cap = int(repo.get("pilot_retained_fuse") or 0)
    if not cases or (cap and len(cases) > cap):
        raise RuntimeError("paused suite is empty or exceeds its retained fuse")
    base_scope, base_manifest = campaign_scope(config)
    scope_manifest = {
        "schema": "programbench_v4_paused_finalization_scope_v1",
        "base_campaign_scope_sha256": base_scope,
        "base_campaign_scope": base_manifest,
        "candidate_sha256": file_sha256(candidates),
        "candidate_count": len(cases),
        "iteration": args.iteration,
        "stop_reason": "suite_witness_capacity_exhausted",
    }
    scope_sha = sha256_json(scope_manifest)
    run_id = f"v4-paused-finalize-{uuid.uuid4().hex}"
    output_root = Path(config["output_root"]).resolve()
    lock = CampaignLock(
        output_root / "campaign.lock",
        campaign_id=config["campaign_id"],
        run_id=run_id,
        config_sha256=scope_sha,
        stale_after_seconds=float(config.get("stale_lock_seconds") or 120),
    ).acquire()
    status = AtomicState(
        repo_root / "paused_finalization_status.json",
        campaign_id=config["campaign_id"],
        run_id=run_id,
    )
    try:
        atomic_write_json(repo_root / "paused_finalization_scope.json", scope_manifest)
        runner = StageRunner(
            adapter=[str(value) for value in repo["stage_adapter"]],
            repo_root=repo_root,
            scope_sha256=scope_sha,
            heartbeat_seconds=float(config.get("heartbeat_seconds") or 10),
            stage_timeout_seconds=float(config.get("stage_timeout_seconds") or 1800),
            status=status,
        )
        responses = {}
        context = {
            "stop_reason": "suite_witness_capacity_exhausted",
            "retained_suite_cases": len(cases),
            "candidate_sha256": scope_manifest["candidate_sha256"],
        }
        for stage in ("final_capture", "full_coverage", "freeze_verification"):
            lock.heartbeat(stage=stage)
            response = runner.run(
                stage,
                iteration=args.iteration,
                repo=repo,
                remaining_seconds=None,
                workflow_context=context,
            )
            _validate_stage_response(stage, response)
            responses[stage] = response
        summary = {
            "schema": "programbench_v4_paused_suite_v1",
            "successful": False,
            "stop_reason": "suite_witness_capacity_exhausted",
            "iteration": args.iteration,
            "retained_suite_cases": len(cases),
            "scope_sha256": scope_sha,
            "candidate_sha256": scope_manifest["candidate_sha256"],
            "final_verification": responses,
            "updated_at": utc_now(),
        }
        atomic_write_json(repo_root / "paused_suite_summary.json", summary)
        status.write(state="paused", stage="paused_incomplete", **summary)
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
