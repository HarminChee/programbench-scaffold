from __future__ import annotations

import hashlib
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from v4.adapters.gofumpt_pilot_adapter import _coverage_comparison_scope

from .candidates import exact_key
from .controller import _repository_recovery_identity
from .io import atomic_write_json, read_json
from .policy import Observation
from .state import utc_now


class LegacyRecoveryError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_response(stage_root: Path) -> dict[str, Any]:
    request_path = stage_root / "request.json"
    response_path = stage_root / "response.json"
    receipt_path = stage_root / "receipt.json"
    if not all(path.is_file() for path in (request_path, response_path, receipt_path)):
        raise LegacyRecoveryError(f"incomplete legacy stage: {stage_root}")
    request, response, receipt = map(read_json, (request_path, response_path, receipt_path))
    if receipt.get("exit_code") != 0 or receipt.get("timed_out"):
        raise LegacyRecoveryError(f"legacy stage did not complete successfully: {stage_root}")
    if request.get("scope_sha256") != response.get("scope_sha256"):
        raise LegacyRecoveryError(f"legacy stage scope mismatch: {stage_root}")
    if receipt.get("scope_sha256") != response.get("scope_sha256"):
        raise LegacyRecoveryError(f"legacy receipt scope mismatch: {stage_root}")
    if receipt.get("response_sha256") != _sha256_file(response_path):
        raise LegacyRecoveryError(f"legacy response digest mismatch: {stage_root}")
    return response


def _quality_valid(path: Path) -> bool:
    report = read_json(path)
    repeat = report.get("repeat_check") or {}
    repeat_returncode = repeat.get("pytest_returncode", repeat.get("returncode", 1))
    return bool(
        report.get("all_dummies_rejected")
        and report.get("all_target_executions_isolated")
        and report.get("all_tests_reject_all_dummies")
        and not report.get("dummy_passing_test_names")
        and int(repeat_returncode) == 0
    )


def _candidate_keys(repo_root: Path, through_iteration: int) -> tuple[list[str], int]:
    keys: set[str] = set()
    attempts = 0
    for iteration in range(1, through_iteration + 1):
        path = repo_root / f"agent_cases/tranche-{iteration:04d}.json"
        if not path.is_file():
            continue
        cases = read_json(path).get("cases") or []
        attempts += len(cases)
        keys.update(exact_key(case) for case in cases)
    return sorted(keys), attempts


def plan_legacy_recovery(
    *, campaign_config: dict[str, Any], campaign_root: Path, instance_id: str
) -> dict[str, Any]:
    repos = {
        str(repo.get("instance_id")): repo
        for repo in campaign_config.get("repositories") or []
    }
    if instance_id not in repos:
        raise LegacyRecoveryError(f"repository is absent from campaign config: {instance_id}")
    repo = repos[instance_id]
    repo_root = campaign_root / "repositories" / instance_id
    if (repo_root / "recovery_checkpoint.json").exists():
        raise LegacyRecoveryError("recovery checkpoint already exists; migration is one-time")
    preflight = read_json(repo_root / "artifacts/preflight.json")
    coverage_binary = Path(str(preflight["coverage_binary"]))
    coverage_binary_sha256 = str(preflight["coverage_binary_sha256"])
    if not coverage_binary.is_file() or _sha256_file(coverage_binary) != coverage_binary_sha256:
        raise LegacyRecoveryError("instrumented coverage binary digest mismatch")
    if str(repo.get("runtime_image_id")) != str(preflight.get("runtime_image_id")):
        # Older preflight artifacts may omit this redundant field, but may not
        # contradict the immutable campaign image.
        if preflight.get("runtime_image_id") is not None:
            raise LegacyRecoveryError("runtime image differs from preflight")

    observations: list[Observation] = []
    accepted_iterations: list[int] = []
    rejected_iteration: int | None = None
    legacy_scopes: set[str] = set()
    for evaluate_path in sorted(
        repo_root.glob("stages/iteration-*/evaluate_marginal/response.json")
    ):
        iteration = int(evaluate_path.parents[1].name.removeprefix("iteration-"))
        stage_root = evaluate_path.parents[1]
        capture = _verified_response(stage_root / "oracle_capture")
        quick_response = _verified_response(stage_root / "quick_coverage")
        quality = _verified_response(stage_root / "quality_gates")
        evaluate = _verified_response(stage_root / "evaluate_marginal")
        legacy_scopes.add(str(evaluate["scope_sha256"]))
        if quality.get("quality_unrepairable") or quality.get("suite_fuse_blocked"):
            break
        quality_path = Path(str(quality.get("quality_report") or ""))
        if not quality_path.is_file() or not _quality_valid(quality_path):
            raise LegacyRecoveryError(f"iteration {iteration} has no valid quality evidence")
        raw_observation = dict(evaluate.get("observation") or {})
        if not raw_observation:
            raise LegacyRecoveryError(f"iteration {iteration} has no marginal observation")
        # The quality stage may perform witness-preserving replacement and
        # re-run complete-suite coverage in the same iteration. Its atomic
        # coverage artifact is therefore authoritative for the subsequent
        # evaluate response; the quick-stage receipt still proves the initial
        # measurement completed successfully.
        coverage_path = repo_root / f"coverage/quick-{iteration:04d}/coverage.json"
        if not coverage_path.is_file():
            raise LegacyRecoveryError(f"iteration {iteration} coverage artifact is missing")
        coverage = read_json(coverage_path)
        if abs(float(coverage["primary_coverage"]) - float(raw_observation["primary_coverage"])) > 1e-9:
            raise LegacyRecoveryError(f"iteration {iteration} coverage response disagrees with observation")
        if int(capture.get("captured_cases") or 0) < int(raw_observation.get("retained_cases") or 0):
            raise LegacyRecoveryError(f"iteration {iteration} capture is smaller than retained suite")
        tranche_path = repo_root / f"agent_cases/tranche-{iteration:04d}.json"
        if not tranche_path.is_file():
            raise LegacyRecoveryError(f"iteration {iteration} generated tranche is missing")
        # Early V4 adapters accidentally reported cumulative retained size in
        # this field. Recovery normalizes it to the actual tranche attempt
        # count so witness-yield density and adaptive scheduling are valid.
        raw_observation["generated_candidates"] = len(
            read_json(tranche_path).get("cases") or []
        )
        raw_observation["coverage_scope_sha256"] = _coverage_comparison_scope(
            coverage,
            coverage_binary_sha256=coverage_binary_sha256,
            language=str(repo.get("language") or ""),
        )
        raw_observation["covered_units"] = tuple(
            sorted(str(unit) for unit in (coverage.get("covered_units") or []))
        )
        observation = Observation(**raw_observation)
        comparable = [row for row in observations if row.coverage_scope_sha256 == observation.coverage_scope_sha256]
        if comparable and observation.primary_coverage < comparable[-1].primary_coverage - 1e-9:
            rejected_iteration = iteration
            break
        observations.append(observation)
        accepted_iterations.append(iteration)

    if not observations:
        raise LegacyRecoveryError("no quality-valid captured observation can be imported")
    last_iteration = accepted_iterations[-1]
    status = read_json(repo_root / "status.json")
    stopped_iteration = int(status.get("iteration") or last_iteration)
    raw_fuse_pause = status.get("stop_reason") == "raw_candidate_fuse_before_capture"
    if raw_fuse_pause and stopped_iteration != last_iteration + 1:
        raise LegacyRecoveryError("raw-fuse pause is not immediately after the accepted tranche")
    rollback_iteration = rejected_iteration or (stopped_iteration if raw_fuse_pause else None)
    if rollback_iteration is not None:
        candidate_source = repo_root / f"candidates/pre-iteration-{rollback_iteration:04d}.json"
    else:
        candidate_source = repo_root / "candidates/current.json"
    if not candidate_source.is_file():
        raise LegacyRecoveryError("accepted candidate snapshot is missing")
    candidate_payload = read_json(candidate_source)
    cases = candidate_payload.get("cases") or []
    if len(cases) != observations[-1].retained_cases:
        raise LegacyRecoveryError("accepted candidate snapshot count disagrees with observation")
    # When witness replacement ran, its selected IDs are the strongest link
    # between the historical capture/quality evidence and this exact snapshot.
    quality = _verified_response(
        repo_root / f"stages/iteration-{last_iteration:04d}/quality_gates"
    )
    replacement = quality.get("witness_replacement") or {}
    selected_ids = set(replacement.get("selected_case_ids") or [])
    if selected_ids and selected_ids != {str(case.get("name") or "") for case in cases}:
        raise LegacyRecoveryError("candidate snapshot does not match witness replacement selection")

    through_iteration = max(stopped_iteration, rejected_iteration or 0, last_iteration)
    unique_keys, attempts = _candidate_keys(repo_root, through_iteration)
    if len(unique_keys) < len(cases):
        raise LegacyRecoveryError("unique raw reservoir is smaller than accepted suite")
    candidate_sha256 = _sha256_file(candidate_source)
    checkpoint = {
        "schema": "programbench_v4_recovery_checkpoint_v1",
        "repository_recovery_identity": _repository_recovery_identity(repo),
        "scope_sha256": sorted(legacy_scopes)[0] if len(legacy_scopes) == 1 else "legacy-mixed-scope",
        "candidate_state_sha256": candidate_sha256,
        "last_completed_iteration": last_iteration,
        "unique_persisted_raw_candidates": len(unique_keys),
        "retained_suite_cases": len(cases),
        "observations": [asdict(row) for row in observations],
        "updated_at": utc_now(),
    }
    return {
        "schema": "programbench_v4_legacy_recovery_plan_v1",
        "instance_id": instance_id,
        "dry_run": True,
        "action": (
            "rollback_coverage_regression"
            if rejected_iteration is not None
            else "rollback_staged_raw_fuse"
            if raw_fuse_pause
            else "import_current_accepted_suite"
        ),
        "accepted_iterations": accepted_iterations,
        "rejected_iteration": rejected_iteration,
        "stopped_iteration": stopped_iteration,
        "candidate_source": str(candidate_source),
        "candidate_source_sha256": candidate_sha256,
        "current_candidate_sha256": _sha256_file(repo_root / "candidates/current.json"),
        "coverage_binary": str(coverage_binary),
        "coverage_binary_sha256": coverage_binary_sha256,
        "runtime_image_id": repo["runtime_image_id"],
        "source_tree_sha256": repo["source_tree_sha256"],
        "unique_persisted_raw_candidates": len(unique_keys),
        "raw_generation_attempts": attempts,
        "unique_candidate_keys": unique_keys,
        "checkpoint": checkpoint,
    }


def apply_legacy_recovery(plan: dict[str, Any], *, campaign_root: Path) -> dict[str, Any]:
    instance_id = str(plan["instance_id"])
    repo_root = campaign_root / "repositories" / instance_id
    checkpoint_path = repo_root / "recovery_checkpoint.json"
    if checkpoint_path.exists():
        raise LegacyRecoveryError("recovery checkpoint already exists; migration is one-time")
    source = Path(str(plan["candidate_source"]))
    if _sha256_file(source) != plan["candidate_source_sha256"]:
        raise LegacyRecoveryError("candidate source changed after dry-run planning")
    migration_root = repo_root / "legacy_recovery" / str(plan["action"])
    if migration_root.exists():
        raise LegacyRecoveryError("legacy recovery audit directory already exists")
    migration_root.mkdir(parents=True)
    current = repo_root / "candidates/current.json"
    shutil.copy2(current, migration_root / "current.before.json")
    raw_ledger = repo_root / "artifacts/raw_candidate_ledger.json"
    if raw_ledger.is_file():
        shutil.copy2(raw_ledger, migration_root / "raw_candidate_ledger.before.json")
    candidate_payload = read_json(source)
    atomic_write_json(current, candidate_payload)
    checkpoint = dict(plan["checkpoint"])
    checkpoint["candidate_state_sha256"] = _sha256_file(current)
    checkpoint["updated_at"] = utc_now()
    atomic_write_json(
        raw_ledger,
        {
            "schema": "programbench_v4_raw_candidate_ledger_v2",
            "iterations": (read_json(migration_root / "raw_candidate_ledger.before.json").get("iterations") or {})
            if (migration_root / "raw_candidate_ledger.before.json").is_file()
            else {},
            "total_generation_attempts": int(plan["raw_generation_attempts"]),
            "unique_candidate_keys": plan["unique_candidate_keys"],
            "unique_persisted_raw_candidates": int(plan["unique_persisted_raw_candidates"]),
            "total_generated_candidates": int(plan["unique_persisted_raw_candidates"]),
        },
    )
    atomic_write_json(checkpoint_path, checkpoint)
    applied = {**plan, "dry_run": False, "applied_at": utc_now(), "checkpoint": checkpoint}
    atomic_write_json(migration_root / "migration_report.json", applied)
    return applied
