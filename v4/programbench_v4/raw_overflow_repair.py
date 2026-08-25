from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from .candidates import exact_key
from .controller import _repository_recovery_identity
from .io import atomic_write_json, read_json, sha256_json
from .state import utc_now


class RawOverflowRepairError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repository(config: dict[str, Any], instance_id: str) -> dict[str, Any]:
    matches = [
        repo
        for repo in config.get("repositories") or []
        if str(repo.get("instance_id")) == instance_id
    ]
    if len(matches) != 1:
        raise RawOverflowRepairError(
            f"expected exactly one configured repository: {instance_id}"
        )
    return matches[0]


def _hard_limit(config: dict[str, Any]) -> int:
    policy = config.get("marginal_policy") or {}
    adaptive = bool(policy.get("adaptive_raw_fuse"))
    value = (
        policy.get("raw_candidate_hard_fuse")
        if adaptive
        else policy.get("raw_candidate_fuse")
    )
    if value is None or int(value) <= 0:
        raise RawOverflowRepairError("campaign has no positive raw hard fuse")
    return int(value)


def _first_seen_candidates(repo_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(repo_root.glob("agent_cases/tranche-*.json")):
        payload = read_json(path)
        for position, case in enumerate(payload.get("cases") or []):
            key = exact_key(case)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "exact_key": key,
                    "case": case,
                    "generation_artifact": str(path),
                    "generation_artifact_sha256": _sha256_file(path),
                    "generation_position": position,
                }
            )
    return rows


def plan_raw_overflow_repair(
    *, campaign_config: dict[str, Any], campaign_root: Path, instance_id: str
) -> dict[str, Any]:
    """Plan a strict one-time repair without modifying campaign artifacts.

    Historical over-limit ledgers are reconstructed in deterministic
    first-generation order.  The accepted suite must be wholly inside the
    admitted prefix; otherwise automatic repair fails closed.
    """

    repo = _repository(campaign_config, instance_id)
    repo_root = campaign_root / "repositories" / instance_id
    audit_root = repo_root / "legacy_recovery" / "raw_hard_fuse_overflow"
    if audit_root.exists():
        raise RawOverflowRepairError("raw overflow repair is one-time")
    ledger_path = repo_root / "artifacts/raw_candidate_ledger.json"
    checkpoint_path = repo_root / "recovery_checkpoint.json"
    candidate_path = repo_root / "candidates/current.json"
    if not all(path.is_file() for path in (ledger_path, checkpoint_path, candidate_path)):
        raise RawOverflowRepairError("ledger, checkpoint, and accepted suite are required")
    ledger = read_json(ledger_path)
    checkpoint = read_json(checkpoint_path)
    if checkpoint.get("repository_recovery_identity") != _repository_recovery_identity(repo):
        raise RawOverflowRepairError("recovery checkpoint target identity changed")
    candidate_sha256 = _sha256_file(candidate_path)
    if checkpoint.get("candidate_state_sha256") != candidate_sha256:
        raise RawOverflowRepairError("accepted candidate state differs from checkpoint")

    ledger_keys = set(str(key) for key in (ledger.get("unique_candidate_keys") or []))
    ledger_count = int(ledger.get("unique_persisted_raw_candidates") or 0)
    if ledger_count != len(ledger_keys):
        raise RawOverflowRepairError("raw ledger count disagrees with exact-key set")
    hard_limit = _hard_limit(campaign_config)
    if ledger_count <= hard_limit:
        raise RawOverflowRepairError("raw ledger does not exceed the configured hard fuse")

    generated = _first_seen_candidates(repo_root)
    generated_keys = {row["exact_key"] for row in generated}
    extra = sorted(generated_keys - ledger_keys)
    if extra:
        raise RawOverflowRepairError(
            "preserved generation artifacts contain exact keys absent from the raw ledger: "
            f"extra={len(extra)}"
        )
    # Some legacy imports retained only exact keys for an earlier reservoir,
    # not the original case payloads.  Those opaque keys can never be safely
    # selected for quarantine: conservatively reserve their capacity first,
    # with a hash/count in the audit report, and take any overflow solely from
    # the deterministic tail of still-preserved generation artifacts.
    opaque_keys = sorted(ledger_keys - generated_keys)
    available_generated_slots = hard_limit - len(opaque_keys)
    if available_generated_slots < 0:
        raise RawOverflowRepairError(
            "legacy opaque exact keys alone exceed the configured hard fuse"
        )
    admitted = generated[:available_generated_slots]
    overflow = generated[available_generated_slots:]
    admitted_keys = set(opaque_keys) | {row["exact_key"] for row in admitted}
    accepted_keys = {
        exact_key(case) for case in (read_json(candidate_path).get("cases") or [])
    }
    if not accepted_keys.issubset(admitted_keys):
        raise RawOverflowRepairError(
            "deterministic overflow would remove an accepted-suite witness"
        )
    checkpoint_count = int(checkpoint.get("unique_persisted_raw_candidates") or 0)
    if checkpoint_count > ledger_count:
        raise RawOverflowRepairError("checkpoint raw count exceeds durable ledger")

    overflow_keys = [row["exact_key"] for row in overflow]
    scope_sha256 = str(checkpoint.get("scope_sha256") or "")
    return {
        "schema": "programbench_v4_raw_overflow_repair_plan_v1",
        "dry_run": True,
        "instance_id": instance_id,
        "reason": "historical_raw_ledger_exceeded_hard_fuse",
        "repository_recovery_identity": _repository_recovery_identity(repo),
        "scope_sha256": scope_sha256,
        "raw_hard_limit": hard_limit,
        "ledger_count_before": ledger_count,
        "checkpoint_count_before": checkpoint_count,
        "ledger_count_after": len(admitted_keys),
        "overflow_unique_candidates": len(overflow),
        "ledger_path": str(ledger_path),
        "ledger_sha256_before": _sha256_file(ledger_path),
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256_before": _sha256_file(checkpoint_path),
        "candidate_path": str(candidate_path),
        "candidate_state_sha256": candidate_sha256,
        "admitted_unique_candidate_keys": sorted(admitted_keys),
        "legacy_opaque_candidate_keys": opaque_keys,
        "legacy_opaque_candidate_keys_sha256": sha256_json(opaque_keys),
        "overflow_exact_keys_sha256": sha256_json(overflow_keys),
        "overflow": overflow,
    }


def apply_raw_overflow_repair(
    plan: dict[str, Any], *, campaign_root: Path
) -> dict[str, Any]:
    instance_id = str(plan["instance_id"])
    repo_root = campaign_root / "repositories" / instance_id
    audit_root = repo_root / "legacy_recovery" / "raw_hard_fuse_overflow"
    if audit_root.exists():
        raise RawOverflowRepairError("raw overflow repair is one-time")
    ledger_path = Path(str(plan["ledger_path"]))
    checkpoint_path = Path(str(plan["checkpoint_path"]))
    candidate_path = Path(str(plan["candidate_path"]))
    if ledger_path.resolve() != (repo_root / "artifacts/raw_candidate_ledger.json").resolve():
        raise RawOverflowRepairError("repair plan raw-ledger path is outside repository state")
    if checkpoint_path.resolve() != (repo_root / "recovery_checkpoint.json").resolve():
        raise RawOverflowRepairError("repair plan checkpoint path is outside repository state")
    if candidate_path.resolve() != (repo_root / "candidates/current.json").resolve():
        raise RawOverflowRepairError("repair plan candidate path is outside repository state")
    if _sha256_file(ledger_path) != plan["ledger_sha256_before"]:
        raise RawOverflowRepairError("raw ledger changed after dry-run planning")
    if _sha256_file(checkpoint_path) != plan["checkpoint_sha256_before"]:
        raise RawOverflowRepairError("checkpoint changed after dry-run planning")
    if _sha256_file(candidate_path) != plan["candidate_state_sha256"]:
        raise RawOverflowRepairError("accepted suite changed after dry-run planning")
    generation_hashes = {
        str(row["generation_artifact"]): str(row["generation_artifact_sha256"])
        for row in plan.get("overflow") or []
    }
    for raw_path, expected in generation_hashes.items():
        path = Path(raw_path)
        if not path.is_file() or _sha256_file(path) != expected:
            raise RawOverflowRepairError(
                "generation artifact changed after dry-run planning"
            )

    audit_root.mkdir(parents=True)
    shutil.copy2(ledger_path, audit_root / "raw_candidate_ledger.before.json")
    shutil.copy2(checkpoint_path, audit_root / "recovery_checkpoint.before.json")
    atomic_write_json(audit_root / "repair_plan.json", plan)

    old_ledger = read_json(ledger_path)
    admitted_keys = list(plan["admitted_unique_candidate_keys"])
    repaired_count = len(admitted_keys)
    repaired_ledger = {
        **old_ledger,
        "schema": "programbench_v4_raw_candidate_ledger_v2",
        "unique_candidate_keys": admitted_keys,
        "unique_persisted_raw_candidates": repaired_count,
        "total_generated_candidates": repaired_count,
        # Deliberately preserve attempt accounting: overflow was generated,
        # merely never admitted for capture.
        "total_generation_attempts": int(
            old_ledger.get("total_generation_attempts")
            or sum(int(value) for value in (old_ledger.get("iterations") or {}).values())
        ),
    }
    overflow_payload = {
        "schema": "programbench_v4_raw_candidate_overflow_v1",
        "reason": "historical_raw_candidate_hard_fuse_capacity_exhausted",
        "scope_sha256": plan["scope_sha256"],
        "repository_recovery_identity": plan["repository_recovery_identity"],
        "raw_candidate_limit": int(plan["raw_hard_limit"]),
        "prior_unique_candidates": int(plan["ledger_count_before"]),
        "admitted_unique_candidates": repaired_count,
        "overflow_unique_candidates": int(plan["overflow_unique_candidates"]),
        "overflow_exact_keys_sha256": plan["overflow_exact_keys_sha256"],
        "cases": plan["overflow"],
    }
    overflow_path = repo_root / "artifacts/raw_candidate_overflow/legacy-repair.json"
    atomic_write_json(overflow_path, overflow_payload)
    atomic_write_json(ledger_path, repaired_ledger)
    checkpoint = read_json(checkpoint_path)
    checkpoint["unique_persisted_raw_candidates"] = repaired_count
    checkpoint["updated_at"] = utc_now()
    atomic_write_json(checkpoint_path, checkpoint)
    applied = {
        **plan,
        "dry_run": False,
        "applied_at": utc_now(),
        "overflow_path": str(overflow_path),
        "overflow_sha256": _sha256_file(overflow_path),
        "ledger_sha256_after": _sha256_file(ledger_path),
        "checkpoint_sha256_after": _sha256_file(checkpoint_path),
    }
    atomic_write_json(audit_root / "repair_report.json", applied)
    return applied
