from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from v4.programbench_v4.controller import _repository_recovery_identity
from v4.programbench_v4.io import atomic_write_json, read_json
from v4.programbench_v4.rust_coverage_migration import (
    RustCoverageMigrationError,
    apply_rust_first_party_migration,
    plan_rust_first_party_migration,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path: Path, *, coverages: tuple[int, int] = (1, 2)) -> tuple[dict, Path, str]:
    instance = "starship__starship.abc1234"
    root = tmp_path / "campaign"
    repo_root = root / "repositories" / instance
    (repo_root / "artifacts").mkdir(parents=True)
    (repo_root / "candidates").mkdir()
    source = tmp_path / "source"
    source.mkdir()
    candidate = repo_root / "candidates/current.json"
    atomic_write_json(candidate, {"cases": [{"name": "accepted", "args": []}]})
    repo = {
        "instance_id": instance,
        "language": "rust",
        "commit": "a" * 40,
        "source_dir": str(source),
        "source_tree_sha256": "b" * 64,
        "runtime_image_id": "sha256:" + "c" * 64,
    }
    config = {"repositories": [repo]}
    binary = tmp_path / "binary"
    binary.write_bytes(b"binary")
    atomic_write_json(
        repo_root / "artifacts/preflight.json",
        {"coverage_binary_sha256": sha(binary)},
    )
    observations = []
    for iteration, first_party_covered in enumerate(coverages, 1):
        export_path = repo_root / f"coverage/quick-{iteration:04d}/out/export.json"
        atomic_write_json(
            export_path,
            {
                "data": [
                    {
                        "files": [
                            {
                                "filename": "/rustc/hash/library/std/src/lib.rs",
                                "summary": {"regions": {"count": 100, "covered": 90}},
                                "segments": [[1, 1, 1, True, True, False]],
                            },
                            {
                                "filename": "/workspace/cargo/registry/src/dep/src/lib.rs",
                                "summary": {"regions": {"count": 50, "covered": 40}},
                                "segments": [[2, 1, 1, True, True, False]],
                            },
                            {
                                "filename": "/workspace/src/src/main.rs",
                                "summary": {
                                    "regions": {"count": 10, "covered": first_party_covered}
                                },
                                "segments": [
                                    [line, 3, 1, True, True, False]
                                    for line in range(1, iteration + 1)
                                ],
                            },
                        ]
                    }
                ]
            },
        )
        old_primary = float(80 + iteration)
        coverage_path = repo_root / f"coverage/quick-{iteration:04d}/coverage.json"
        atomic_write_json(
            coverage_path,
            {
                "primary_coverage": old_primary,
                "total_regions": 160,
                "covered_regions": 131,
                "covered_units": ["/rustc/lib.rs:1:1", "/workspace/src/src/main.rs:1:1"],
                "profile": str(export_path),
                "sample_policy": "complete_retained_suite",
                "coverage_valid": True,
            },
        )
        observation = {
            "primary_coverage": old_primary,
            "coverage_scope_sha256": "d" * 64,
            "retained_cases": iteration,
            "generated_candidates": 1,
            "wall_seconds": 1.0,
            "new_coverage_units": 1,
            "covered_units": ["/rustc/lib.rs:1:1"],
            "promoted": True,
        }
        observations.append(observation)
        atomic_write_json(
            repo_root
            / f"stages/iteration-{iteration:04d}/evaluate_marginal/response.json",
            {"observation": observation},
        )
    atomic_write_json(
        repo_root / "artifacts/witness_ledger.json",
        {"coverage_units": ["/rustc/lib.rs:1:1"], "behaviors": ["existing"]},
    )
    atomic_write_json(
        repo_root / "recovery_checkpoint.json",
        {
            "schema": "programbench_v4_recovery_checkpoint_v1",
            "repository_recovery_identity": _repository_recovery_identity(repo),
            "scope_sha256": "e" * 64,
            "candidate_state_sha256": sha(candidate),
            "last_completed_iteration": 2,
            "unique_persisted_raw_candidates": 2,
            "retained_suite_cases": 2,
            "observations": observations,
        },
    )
    return config, root, instance


def test_rust_coverage_migration_rewrites_only_first_party_evidence(tmp_path: Path) -> None:
    config, root, instance = fixture(tmp_path)
    repo_root = root / "repositories" / instance
    plan = plan_rust_first_party_migration(
        campaign_config=config, campaign_root=root, instance_id=instance
    )
    assert plan["dry_run"]
    assert plan["safe_to_apply"]
    assert plan["first_party_denominator"] == 10
    assert plan["updates"][-1]["excluded_exact_units"] == 2
    assert plan["migrated_observations"][-1]["primary_coverage"] == 20.0
    assert plan["migrated_observations"][-1]["covered_units"] == (
        "src/main.rs:1:3",
        "src/main.rs:2:3",
    )

    applied = apply_rust_first_party_migration(plan, campaign_root=root)
    assert not applied["dry_run"]
    checkpoint = read_json(repo_root / "recovery_checkpoint.json")
    assert checkpoint["coverage_migration"]["source_scope"] == "/workspace/src"
    assert checkpoint["observations"][-1]["primary_coverage"] == 20.0
    ledger = read_json(repo_root / "artifacts/witness_ledger.json")
    assert ledger["coverage_units"] == ["src/main.rs:1:3", "src/main.rs:2:3"]
    assert ledger["behaviors"] == ["existing"]
    assert (
        repo_root
        / "legacy_recovery/rust_first_party_coverage/recovery_checkpoint.before.json"
    ).is_file()
    with pytest.raises(RustCoverageMigrationError, match="one-time"):
        apply_rust_first_party_migration(plan, campaign_root=root)


def test_rust_coverage_migration_requires_revalidation_if_filtered_scalar_regresses(
    tmp_path: Path,
) -> None:
    config, root, instance = fixture(tmp_path, coverages=(5, 4))
    plan = plan_rust_first_party_migration(
        campaign_config=config, campaign_root=root, instance_id=instance
    )
    assert not plan["safe_to_apply"]
    assert plan["nonmonotonic_primary_pairs"] == [[50.0, 40.0]]
    with pytest.raises(RustCoverageMigrationError, match="fresh suite revalidation"):
        apply_rust_first_party_migration(plan, campaign_root=root)
