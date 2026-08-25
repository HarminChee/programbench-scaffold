from __future__ import annotations

import hashlib
import json

import pytest

from v4.programbench_v4.recovery_tail import (
    apply_unpromoted_tail_repair,
    inspect_unpromoted_tail,
)


def _write_repo(tmp_path, *, tail_novelty: int = 0):
    repo = tmp_path / "repo"
    (repo / "candidates").mkdir(parents=True)
    candidate = repo / "candidates/current.json"
    candidate.write_text('{"cases":[{"name":"a"}]}\n', encoding="utf-8")
    scope = "a" * 64
    basis = {
        "promoted": True,
        "coverage_valid": True,
        "quality_passed": True,
        "coverage_scope_sha256": scope,
        "retained_cases": 1,
        "primary_coverage": 42.0,
    }
    tail = {
        **basis,
        "promoted": False,
        "new_coverage_units": tail_novelty,
        "new_behavior_families": 0,
        "new_error_classes": 0,
        "new_fixture_shapes": 0,
        "new_assertion_classes": 0,
        "new_state_transitions": 0,
    }
    checkpoint = {
        "candidate_state_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "observations": [basis, tail, dict(tail)],
        "retained_suite_cases": 1,
    }
    (repo / "recovery_checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")
    return repo


def test_trims_only_zero_novelty_unpromoted_tail(tmp_path):
    repo = _write_repo(tmp_path)
    assert inspect_unpromoted_tail(repo)["trimmed_observations"] == 2
    receipt = apply_unpromoted_tail_repair(repo)
    checkpoint = json.loads((repo / "recovery_checkpoint.json").read_text())
    assert len(checkpoint["observations"]) == 1
    assert receipt["trimmed_observations"] == 2
    assert (repo / "recovery_repairs").is_dir()


def test_refuses_tail_with_novelty(tmp_path):
    repo = _write_repo(tmp_path, tail_novelty=1)
    with pytest.raises(ValueError, match="novelty"):
        inspect_unpromoted_tail(repo)
