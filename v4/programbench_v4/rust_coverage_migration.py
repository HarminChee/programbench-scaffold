from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from v4.adapters.gofumpt_pilot_adapter import (
    RUST_FIRST_PARTY_COVERAGE_SCHEMA,
    _coverage_comparison_scope,
    _rust_export_summary,
)

from .controller import _repository_recovery_identity
from .io import atomic_write_json, read_json
from .state import utc_now


class RustCoverageMigrationError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo(config: dict[str, Any], instance_id: str) -> dict[str, Any]:
    rows = [
        row
        for row in config.get("repositories") or []
        if str(row.get("instance_id")) == instance_id
    ]
    if len(rows) != 1 or str(rows[0].get("language")) != "rust":
        raise RustCoverageMigrationError("expected one configured Rust repository")
    return rows[0]


def _observation_iterations(
    repo_root: Path, observations: list[dict[str, Any]], last_iteration: int
) -> list[int]:
    mapped: list[int] = []
    cursor = 1
    for observation in observations:
        match: int | None = None
        for iteration in range(cursor, last_iteration + 1):
            path = repo_root / f"stages/iteration-{iteration:04d}/evaluate_marginal/response.json"
            if not path.is_file():
                continue
            staged = read_json(path).get("observation") or {}
            if (
                int(staged.get("retained_cases") or -1)
                == int(observation.get("retained_cases") or -2)
                and int(staged.get("generated_candidates") or -1)
                == int(observation.get("generated_candidates") or -2)
                and abs(
                    float(staged.get("primary_coverage") or 0.0)
                    - float(observation.get("primary_coverage") or 0.0)
                )
                <= 1e-9
            ):
                match = iteration
                break
        if match is None:
            raise RustCoverageMigrationError(
                "checkpoint observation cannot be mapped to an immutable iteration"
            )
        mapped.append(match)
        cursor = match + 1
    return mapped


def plan_rust_first_party_migration(
    *, campaign_config: dict[str, Any], campaign_root: Path, instance_id: str
) -> dict[str, Any]:
    repo = _repo(campaign_config, instance_id)
    repo_root = campaign_root / "repositories" / instance_id
    audit_root = repo_root / "legacy_recovery/rust_first_party_coverage"
    if audit_root.exists():
        raise RustCoverageMigrationError("Rust coverage migration is one-time")
    checkpoint_path = repo_root / "recovery_checkpoint.json"
    candidate_path = repo_root / "candidates/current.json"
    if not checkpoint_path.is_file():
        raise RustCoverageMigrationError("recovery checkpoint is missing")
    checkpoint = read_json(checkpoint_path)
    if checkpoint.get("repository_recovery_identity") != _repository_recovery_identity(repo):
        raise RustCoverageMigrationError("repository identity changed")
    expected_candidate_sha = str(checkpoint.get("candidate_state_sha256") or "")
    candidate_evidence = None
    if candidate_path.is_file() and _sha(candidate_path) == expected_candidate_sha:
        candidate_evidence = candidate_path
    else:
        for path in sorted((repo_root / "candidates").glob("pre-iteration-*.json"), reverse=True):
            if _sha(path) == expected_candidate_sha:
                candidate_evidence = path
                break
    if candidate_evidence is None:
        raise RustCoverageMigrationError("checkpoint candidate digest has no preserved snapshot")
    observations = [dict(row) for row in checkpoint.get("observations") or []]
    if not observations:
        raise RustCoverageMigrationError("checkpoint has no observations")
    iterations = _observation_iterations(
        repo_root, observations, int(checkpoint.get("last_completed_iteration") or 0)
    )
    preflight = read_json(repo_root / "artifacts/preflight.json")
    binary_sha = str(preflight.get("coverage_binary_sha256") or "")
    if not binary_sha:
        raise RustCoverageMigrationError("coverage binary digest is missing")

    updates: list[dict[str, Any]] = []
    migrated_observations: list[dict[str, Any]] = []
    cumulative_units: set[str] = set()
    denominators: set[int] = set()
    promoted_primary: list[float] = []
    promoted_unit_sets: list[set[str]] = []
    for observation, iteration in zip(observations, iterations):
        coverage_path = repo_root / f"coverage/quick-{iteration:04d}/coverage.json"
        if not coverage_path.is_file():
            raise RustCoverageMigrationError(f"iteration {iteration} coverage is missing")
        old = read_json(coverage_path)
        export_path = Path(str(old.get("profile") or ""))
        if not export_path.is_file():
            raise RustCoverageMigrationError(f"iteration {iteration} LLVM export is missing")
        covered, total, units = _rust_export_summary(read_json(export_path))
        denominators.add(total)
        filtered = {
            **old,
            "primary_coverage": 100.0 * covered / total,
            "covered_regions": covered,
            "total_regions": total,
            "covered_units": sorted(units),
            "coverage_valid": True,
            "coverage_filter_schema": RUST_FIRST_PARTY_COVERAGE_SCHEMA,
            "source_scope": "/workspace/src",
        }
        migrated = {
            **observation,
            "primary_coverage": filtered["primary_coverage"],
            "coverage_scope_sha256": _coverage_comparison_scope(
                filtered, coverage_binary_sha256=binary_sha, language="rust"
            ),
            "new_coverage_units": len(units - cumulative_units),
            "covered_units": tuple(sorted(units)),
        }
        cumulative_units.update(units)
        if migrated.get("promoted", True) and migrated.get("quality_passed", True) and migrated.get("coverage_valid", True):
            promoted_primary.append(float(migrated["primary_coverage"]))
            promoted_unit_sets.append(set(units))
        updates.append(
            {
                "iteration": iteration,
                "coverage_path": str(coverage_path),
                "coverage_sha256_before": _sha(coverage_path),
                "export_path": str(export_path),
                "export_sha256": _sha(export_path),
                "excluded_exact_units": len(
                    {str(unit) for unit in (old.get("covered_units") or [])} - units
                ),
                "filtered_coverage": filtered,
            }
        )
        migrated_observations.append(migrated)
    if len(denominators) != 1:
        raise RustCoverageMigrationError("first-party denominator changed across observations")
    nonmonotonic = [
        [before, after]
        for before, after in zip(promoted_primary, promoted_primary[1:])
        if after < before - 1e-9
    ]
    exact_regressions = [
        sorted(before - after)
        for before, after in zip(promoted_unit_sets, promoted_unit_sets[1:])
        if before - after
    ]
    return {
        "schema": "programbench_v4_rust_first_party_migration_plan_v1",
        "dry_run": True,
        "instance_id": instance_id,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256_before": _sha(checkpoint_path),
        "candidate_evidence": str(candidate_evidence),
        "candidate_state_sha256": expected_candidate_sha,
        "coverage_binary_sha256": binary_sha,
        "coverage_filter_schema": RUST_FIRST_PARTY_COVERAGE_SCHEMA,
        "source_scope": "/workspace/src",
        "first_party_denominator": next(iter(denominators)),
        "updates": updates,
        "migrated_observations": migrated_observations,
        "cumulative_first_party_units": sorted(cumulative_units),
        "nonmonotonic_primary_pairs": nonmonotonic,
        "exact_unit_regressions": exact_regressions,
        "safe_to_apply": not nonmonotonic and not exact_regressions,
    }


def apply_rust_first_party_migration(
    plan: dict[str, Any], *, campaign_root: Path
) -> dict[str, Any]:
    if not plan.get("safe_to_apply"):
        raise RustCoverageMigrationError(
            "filtered history regresses and requires fresh suite revalidation"
        )
    instance_id = str(plan["instance_id"])
    repo_root = campaign_root / "repositories" / instance_id
    audit_root = repo_root / "legacy_recovery/rust_first_party_coverage"
    if audit_root.exists():
        raise RustCoverageMigrationError("Rust coverage migration is one-time")
    checkpoint_path = Path(str(plan["checkpoint_path"]))
    if checkpoint_path.resolve() != (repo_root / "recovery_checkpoint.json").resolve():
        raise RustCoverageMigrationError("checkpoint path escaped repository state")
    if _sha(checkpoint_path) != plan["checkpoint_sha256_before"]:
        raise RustCoverageMigrationError("checkpoint changed after dry-run")
    for update in plan["updates"]:
        coverage_path = Path(str(update["coverage_path"]))
        export_path = Path(str(update["export_path"]))
        if _sha(coverage_path) != update["coverage_sha256_before"] or _sha(export_path) != update["export_sha256"]:
            raise RustCoverageMigrationError("coverage evidence changed after dry-run")

    audit_root.mkdir(parents=True)
    shutil.copy2(checkpoint_path, audit_root / "recovery_checkpoint.before.json")
    ledger_path = repo_root / "artifacts/witness_ledger.json"
    if ledger_path.is_file():
        shutil.copy2(ledger_path, audit_root / "witness_ledger.before.json")
    atomic_write_json(audit_root / "migration_plan.json", plan)
    for update in plan["updates"]:
        coverage_path = Path(str(update["coverage_path"]))
        shutil.copy2(
            coverage_path,
            audit_root / f"quick-{int(update['iteration']):04d}.coverage.before.json",
        )
        atomic_write_json(coverage_path, update["filtered_coverage"])
    checkpoint = read_json(checkpoint_path)
    checkpoint["observations"] = plan["migrated_observations"]
    checkpoint["updated_at"] = utc_now()
    checkpoint["coverage_migration"] = {
        "schema": plan["coverage_filter_schema"],
        "source_scope": plan["source_scope"],
        "first_party_denominator": plan["first_party_denominator"],
    }
    atomic_write_json(checkpoint_path, checkpoint)
    ledger = read_json(ledger_path) if ledger_path.is_file() else {}
    ledger["coverage_units"] = plan["cumulative_first_party_units"]
    ledger["coverage_filter_schema"] = plan["coverage_filter_schema"]
    ledger["source_scope"] = plan["source_scope"]
    atomic_write_json(ledger_path, ledger)
    result = {
        **plan,
        "dry_run": False,
        "applied_at": utc_now(),
        "checkpoint_sha256_after": _sha(checkpoint_path),
    }
    atomic_write_json(audit_root / "migration_report.json", result)
    return result
