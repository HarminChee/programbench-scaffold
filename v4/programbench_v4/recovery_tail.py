from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import atomic_write_json, read_json


NOVELTY_FIELDS = (
    "new_coverage_units",
    "new_behavior_families",
    "new_error_classes",
    "new_fixture_shapes",
    "new_assertion_classes",
    "new_state_transitions",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_unpromoted_tail(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    checkpoint_path = repo_root / "recovery_checkpoint.json"
    candidate_path = repo_root / "candidates/current.json"
    checkpoint = read_json(checkpoint_path)
    observations = checkpoint.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("checkpoint has no observations")
    candidate_sha = _sha256(candidate_path)
    if candidate_sha != checkpoint.get("candidate_state_sha256"):
        raise ValueError("current candidate does not match recovery checkpoint")

    promoted_index = -1
    for index, row in enumerate(observations):
        if row.get("promoted") is True and row.get("coverage_valid") is True:
            promoted_index = index
    if promoted_index < 0 or promoted_index == len(observations) - 1:
        raise ValueError("checkpoint has no trailing unpromoted observations")

    basis = observations[promoted_index]
    tail = observations[promoted_index + 1 :]
    for row in tail:
        if row.get("promoted") is not False:
            raise ValueError("tail contains a promoted observation")
        if row.get("coverage_valid") is not True or row.get("quality_passed") is not True:
            raise ValueError("tail is not coverage-valid and quality-passing")
        if row.get("coverage_scope_sha256") != basis.get("coverage_scope_sha256"):
            raise ValueError("tail coverage scope differs from promoted basis")
        if int(row.get("retained_cases") or 0) != int(basis.get("retained_cases") or 0):
            raise ValueError("tail changed the retained accepted suite")
        if any(int(row.get(field) or 0) != 0 for field in NOVELTY_FIELDS):
            raise ValueError("tail contains novelty evidence and cannot be trimmed")

    return {
        "repo_root": str(repo_root),
        "checkpoint_path": str(checkpoint_path),
        "candidate_sha256": candidate_sha,
        "observation_count_before": len(observations),
        "observation_count_after": promoted_index + 1,
        "trimmed_observations": len(tail),
        "promoted_primary_coverage": basis.get("primary_coverage"),
        "coverage_scope_sha256": basis.get("coverage_scope_sha256"),
        "checkpoint": checkpoint,
        "trimmed_checkpoint": {**checkpoint, "observations": observations[: promoted_index + 1]},
    }


def apply_unpromoted_tail_repair(repo_root: Path) -> dict[str, Any]:
    report = inspect_unpromoted_tail(repo_root)
    root = Path(report["repo_root"])
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = root / "recovery_repairs" / f"trim-unpromoted-tail-{timestamp}"
    archive.mkdir(parents=True, exist_ok=False)
    shutil.copy2(root / "recovery_checkpoint.json", archive / "recovery_checkpoint.before.json")
    if (root / "status.json").exists():
        shutil.copy2(root / "status.json", archive / "status.before.json")
    trimmed = report.pop("trimmed_checkpoint")
    report.pop("checkpoint")
    receipt = {
        "schema": "programbench_v4_unpromoted_tail_repair_v1",
        **report,
        "archive": str(archive),
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(archive / "receipt.json", receipt)
    atomic_write_json(root / "recovery_checkpoint.json", trimmed)
    return receipt
