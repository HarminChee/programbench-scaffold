from __future__ import annotations

import os
import json
from pathlib import Path

import pytest

from v4.programbench_v4.io import atomic_write_json
from v4.programbench_v4.isolation import (
    ContainerContract,
    Mount,
    validate_execution_provenance,
    validate_pb_generation_scope,
)
from v4.programbench_v4.provenance import source_tree_sha256, validate_repository_scope


def test_gofumpt_coverage_mounts_target_next_to_oracle_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    repo_root = tmp_path / "repo"
    artifacts = repo_root / "artifacts"
    bundle = tmp_path / "bundle"
    (bundle / "eval").mkdir(parents=True)
    artifacts.mkdir(parents=True)
    coverage_binary = tmp_path / "coverage-binary"
    coverage_binary.write_bytes(b"binary")
    (bundle / "eval/generated_cli_manifest.json").write_text(
        json.dumps({"cases": [{"id": str(index)} for index in range(8)]}), encoding="utf-8"
    )
    (artifacts / "current_bundle.json").write_text(
        json.dumps({"bundle_root": str(bundle)}), encoding="utf-8"
    )
    (artifacts / "preflight.json").write_text(
        json.dumps({"coverage_binary": str(coverage_binary)}), encoding="utf-8"
    )
    observed: list[tuple[adapter.Mount, ...]] = []
    scripts: list[str] = []

    def fake_run_container(**kwargs: object) -> None:
        mounts = tuple(kwargs["mounts"])  # type: ignore[arg-type]
        observed.append(mounts)
        scripts.append(str(kwargs["script"]))
        for mount in mounts:
            if mount.target == "/out":
                (mount.source / "profile.txt").write_text(
                    "mode: set\nexample.go:1.1,1.2 1 1\n", encoding="utf-8"
                )

    monkeypatch.setattr(adapter, "run_container", fake_run_container)
    result = adapter._coverage_profile(
        {
            "iteration": 1,
            "repository": {
                "runtime_image_id": "sha256:" + "c" * 64,
                "go_toolchain_root": str(tmp_path / "go"),
            },
        },
        repo_root,
        full=False,
    )
    first_mounts = {mount.target: mount for mount in observed[0]}
    assert "/oracle" in first_mounts
    assert "/workspace/executable" not in first_mounts
    assert (first_mounts["/oracle"].source / "executable").read_bytes() == b"binary"
    assert first_mounts["/oracle"].readonly is True
    assert " -k " not in scripts[0]
    assert result["coverage"]["sampled_cases"] == 8
    assert result["coverage"]["sample_policy"] == "complete_retained_suite"
    assert "GOTMPDIR=/workspace/gotmp" in scripts[1]


def test_rust_coverage_summary_is_strictly_first_party() -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    def row(filename: str, count: int, covered: int, execution: int) -> dict:
        return {
            "filename": filename,
            "summary": {"regions": {"count": count, "covered": covered}},
            "segments": [[7, 3, execution, True, True, False]],
        }

    export = {
        "data": [
            {
                "files": [
                    row("/rustc/hash/library/std/src/lib.rs", 100, 99, 1),
                    row("/workspace/cargo/registry/src/crate/src/lib.rs", 200, 150, 1),
                    row("/workspace/src/target/debug/build/generated.rs", 50, 50, 1),
                    row("/workspace/src/src/main.rs", 10, 4, 1),
                    row("/workspace/src/crates/core/src/lib.rs", 20, 6, 0),
                ]
            }
        ]
    }
    covered, total, units = adapter._rust_export_summary(export)
    assert (covered, total) == (10, 30)
    assert units == {"src/main.rs:7:3"}
    assert all(
        "rustc" not in unit and "registry" not in unit and "target" not in unit
        for unit in units
    )


def test_rust_coverage_summary_rejects_empty_or_wrong_source_scope() -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    export = {
        "data": [
            {
                "files": [
                    {
                        "filename": "/workspace/cargo/registry/src/crate/src/lib.rs",
                        "summary": {"regions": {"count": 10, "covered": 10}},
                        "segments": [[1, 1, 1, True, True, False]],
                    }
                ]
            }
        ]
    }
    with pytest.raises(RuntimeError, match="no first-party Rust regions"):
        adapter._rust_export_summary(export)


def test_rust_preflight_limits_inner_cargo_parallelism(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    repo_root = tmp_path / "repo"
    source = tmp_path / "source"
    rust_root = tmp_path / "rust"
    dependency_cache = tmp_path / "dependency-cache"
    cargo_llvm_cov = tmp_path / "cargo-llvm-cov"
    for path in (source, rust_root, dependency_cache):
        path.mkdir(parents=True)
    cargo_llvm_cov.write_bytes(b"cargo-llvm-cov")
    monkeypatch.setattr(
        adapter,
        "validate_dependency_cache",
        lambda *_args, **_kwargs: {"cache_dir": str(dependency_cache)},
    )
    scripts: list[str] = []
    memories: list[str] = []

    def fake_run_container(**kwargs: object) -> None:
        scripts.append(str(kwargs["script"]))
        memories.append(str(kwargs["memory"]))
        mounts = tuple(kwargs["mounts"])  # type: ignore[arg-type]
        output = next(mount.source for mount in mounts if mount.target == "/out")
        output.mkdir(parents=True, exist_ok=True)
        (output / "reference_executable").write_bytes(b"reference")
        (output / "coverage_executable").write_bytes(b"coverage")
        (output / "native.coverage.json").write_text(
            json.dumps({"data": []}), encoding="utf-8"
        )
        (output / "native.status.json").write_text(
            json.dumps({"native_test_returncode": 0, "native_coverage_returncode": 0}),
            encoding="utf-8",
        )

    monkeypatch.setattr(adapter, "run_container", fake_run_container)
    monkeypatch.setattr(adapter, "cleanroom_image", lambda *_args, **_kwargs: "sha256:" + "d" * 64)
    adapter.preflight_rust(
        {
            "repository": {
                "instance_id": "rust-repo",
                "runtime_image_id": "sha256:" + "c" * 64,
                "runtime_image_reference": "runtime:fixed",
                "source_dir": str(source),
                "rust_toolchain_root": str(rust_root),
                "rust_cargo_llvm_cov_binary": str(cargo_llvm_cov),
                "rust_cargo_llvm_cov_sha256": adapter.sha256_file(cargo_llvm_cov),
                "binary_name": "example",
            }
        },
        repo_root,
    )
    assert "export CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-1}" in scripts[0]
    assert memories == ["6g"]


def test_gofumpt_dummy_failure_indexes_are_exact() -> None:
    from v4.adapters.gofumpt_pilot_adapter import (
        dummy_failure_case_names,
        dummy_failure_indexes,
    )

    report = {
        "dummy_passing_test_names": [
            "eval.tests.test_generated_cli_oracle.test_0010_old_language",
            "eval.tests.test_generated_cli_oracle.test_0003_whitespace",
            "malformed-name",
            "eval.tests.test_generated_cli_oracle.test_0003_whitespace",
        ]
    }
    assert dummy_failure_indexes(report) == [3, 10]
    manifest = [{"name": f"case-{index}"} for index in range(11)]
    assert dummy_failure_case_names(report, manifest) == ["case-10", "case-3"]


def test_agent_case_name_is_canonical_before_capture() -> None:
    from v4.adapters.gofumpt_pilot_adapter import normalize_agent_cases

    cases = normalize_agent_cases(
        {"cases": [{"name": "lang-go1.12 / legacy", "args": [], "stdin": "package p\n"}]},
        iteration=2,
    )
    assert cases[0]["name"] == "v4_t02_000_lang_go1_12_legacy"


def test_gofumpt_capture_scope_changes_with_candidate_content(tmp_path: Path) -> None:
    from v4.adapters.gofumpt_pilot_adapter import capture_scope_id

    cases = tmp_path / "cases.json"
    cases.write_text('{"cases":[{"name":"one"}]}', encoding="utf-8")
    first = capture_scope_id(cases)
    cases.write_text('{"cases":[{"name":"two"}]}', encoding="utf-8")
    second = capture_scope_id(cases)
    assert len(first) == 16
    assert first != second


def test_gofumpt_collects_distinct_exact_coverage_witnesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    repo_root = tmp_path / "repo"
    bundle = tmp_path / "bundle"
    (repo_root / "artifacts").mkdir(parents=True)
    (repo_root / "candidates").mkdir()
    (bundle / "eval/tests").mkdir(parents=True)
    binary = tmp_path / "coverage"
    binary.write_bytes(b"coverage")
    candidates = [
        {"name": "left", "stdin": "package p\n"},
        {"name": "right", "stdin": "package q\n"},
    ]
    manifest = {
        "cases": [
            {"name": "left", "returncode": 0, "stdout_sha256": "a", "stderr_sha256": "e"},
            {"name": "right", "returncode": 0, "stdout_sha256": "a", "stderr_sha256": "e"},
        ]
    }
    (repo_root / "candidates/current.json").write_text(
        json.dumps({"cases": candidates}), encoding="utf-8"
    )
    (repo_root / "artifacts/current_bundle.json").write_text(
        json.dumps({"bundle_root": str(bundle), "cases_scope_sha256": "scope"}), encoding="utf-8"
    )
    (repo_root / "artifacts/preflight.json").write_text(
        json.dumps({"coverage_binary": str(binary)}), encoding="utf-8"
    )
    (bundle / "eval/generated_cli_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (bundle / "eval/tests/test_generated_cli_oracle.py").write_text(
        "def test_0000_left():\n    pass\n\ndef test_0001_right():\n    pass\n",
        encoding="utf-8",
    )

    def fake_run_container(**kwargs: object) -> None:
        mounts = {mount.target: mount for mount in kwargs["mounts"]}  # type: ignore[index]
        if "/profiles" in mounts:
            profiles = mounts["/profiles"].source
            (profiles / "0000.txt").write_text(
                "mode: set\nfile.go:1.1,1.2 1 1\n", encoding="utf-8"
            )
            (profiles / "0001.txt").write_text(
                "mode: set\nfile.go:2.1,2.2 1 1\n", encoding="utf-8"
            )

    monkeypatch.setattr(adapter, "run_container", fake_run_container)
    witness_map, payload = adapter.collect_exact_case_witnesses(
        {
            "iteration": 3,
            "repository": {
                "runtime_image_id": "sha256:" + "d" * 64,
                "go_toolchain_root": str(tmp_path / "go"),
            },
        },
        repo_root,
    )
    assert "coverage:file.go:1.1,1.2" in witness_map["left"]
    assert "coverage:file.go:2.1,2.2" in witness_map["right"]
    assert payload["case_count"] == 2


def test_behavior_witness_is_a_family_not_a_golden_output_identity() -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    left = adapter._case_static_witnesses(
        {
            "returncode": 0,
            "stdout_sha256": "left-output",
            "stdout_bytes": 10,
            "stderr_sha256": "empty",
            "stderr_bytes": 0,
        },
        {"stdin": "package left\n", "args": ["-lang=go1.20"]},
    )
    right = adapter._case_static_witnesses(
        {
            "returncode": 0,
            "stdout_sha256": "right-output",
            "stdout_bytes": 99,
            "stderr_sha256": "empty",
            "stderr_bytes": 0,
        },
        {"stdin": "package right\n", "args": ["-lang=go1.21"]},
    )
    assert {value for value in left if value.startswith("behavior:")} == {
        value for value in right if value.startswith("behavior:")
    }


def test_raw_candidate_ledger_survives_suite_replacement(tmp_path: Path) -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    repo_root = tmp_path / "repo"
    (repo_root / "agent_cases").mkdir(parents=True)
    first = [{"name": f"first-{index}", "args": [str(index)]} for index in range(2)]
    second = [{"name": f"second-{index}", "args": [str(index)]} for index in range(3)]
    (repo_root / "agent_cases/tranche-0001.json").write_text(
        json.dumps({"cases": first}), encoding="utf-8"
    )
    response1 = adapter.static_select(
        {
            "iteration": 1,
            "workflow_context": {"recommended_tranche_size": 8},
            "repository": {"pilot_raw_fuse": 20, "pilot_retained_fuse": 4},
        },
        repo_root,
    )
    assert response1["cumulative_raw_candidates"] == 2
    # Simulate a witness replacement that shrinks the retained suite.
    (repo_root / "candidates/current.json").write_text(
        json.dumps({"cases": first[:1]}), encoding="utf-8"
    )
    (repo_root / "agent_cases/tranche-0002.json").write_text(
        json.dumps({"cases": second}), encoding="utf-8"
    )
    response2 = adapter.static_select(
        {
            "iteration": 2,
            "workflow_context": {"recommended_tranche_size": 8},
            "repository": {"pilot_raw_fuse": 20, "pilot_retained_fuse": 4},
        },
        repo_root,
    )
    assert response2["cumulative_raw_candidates"] == 3
    assert response2["unique_persisted_raw_candidates"] == 3
    assert response2["raw_generation_attempts"] == 5
    # One second-tranche row is behaviorally identical by exact_key. Attempts
    # remain auditable, but retries/duplicates do not consume the unique raw
    # reservoir fuse twice.
    assert response2["cumulative_cases"] == 3


def test_deferred_reservoir_is_periodically_reranked_without_loss(tmp_path: Path) -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    repo_root = tmp_path / "repo"
    (repo_root / "agent_cases").mkdir(parents=True)
    first = [{"name": f"first-{index}", "args": [str(index)]} for index in range(3)]
    (repo_root / "agent_cases/tranche-0001.json").write_text(
        json.dumps({"cases": first}), encoding="utf-8"
    )
    response1 = adapter.static_select(
        {
            "iteration": 1,
            "workflow_context": {
                "recommended_tranche_size": 1,
                "reservoir_rerank_period": 1,
            },
            "repository": {"pilot_raw_fuse": 20},
        },
        repo_root,
    )
    assert response1["cumulative_cases"] == 1
    reservoir = json.loads((repo_root / "candidates/reservoir.json").read_text())
    assert len(reservoir["cases"]) == 2

    second = [{"name": "fresh", "args": ["fresh"]}]
    (repo_root / "agent_cases/tranche-0002.json").write_text(
        json.dumps({"cases": second}), encoding="utf-8"
    )
    response2 = adapter.static_select(
        {
            "iteration": 2,
            "workflow_context": {
                "recommended_tranche_size": 1,
                "reservoir_rerank_period": 1,
            },
            "repository": {"pilot_raw_fuse": 20},
        },
        repo_root,
    )
    assert response2["reservoir_reranked"] is True
    assert response2["reservoir_cases_before"] == 2
    assert response2["reservoir_cases_after"] >= 1
    assert response2["cumulative_cases"] == 2


def test_coverage_comparison_scope_includes_metric_denominator_and_sample_policy() -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    complete = {
        "total_statements": 100,
        "sample_policy": "complete_retained_suite",
    }
    base = adapter._coverage_comparison_scope(
        complete, coverage_binary_sha256="a" * 64, language="go"
    )
    assert base != adapter._coverage_comparison_scope(
        {**complete, "sample_policy": "rotating_sample"},
        coverage_binary_sha256="a" * 64,
        language="go",
    )
    assert base != adapter._coverage_comparison_scope(
        {**complete, "total_statements": 101},
        coverage_binary_sha256="a" * 64,
        language="go",
    )
    assert base != adapter._coverage_comparison_scope(
        {
            "total_regions": 100,
            "sample_policy": "complete_retained_suite",
            "coverage_filter_schema": adapter.RUST_FIRST_PARTY_COVERAGE_SCHEMA,
            "source_scope": "/workspace/src",
        },
        coverage_binary_sha256="a" * 64,
        language="rust",
    )
    with pytest.raises(RuntimeError, match="not first-party filtered"):
        adapter._coverage_comparison_scope(
            {"total_regions": 100, "sample_policy": "complete_retained_suite"},
            coverage_binary_sha256="a" * 64,
            language="rust",
        )


def test_legacy_scalar_rebases_only_when_exact_coverage_units_do_not_regress() -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    result = adapter._revalidate_recovery_baseline(
        legacy_observation={
            "primary_coverage": 47.7343,
            "coverage_scope_sha256": "a" * 64,
            "retained_cases": 225,
            "generated_candidates": 24,
            "wall_seconds": 1,
        },
        legacy_units={"block-a", "block-b"},
        restored={
            "primary_coverage": 47.5481,
            "covered_units": ["block-a", "block-b", "block-c"],
        },
        coverage_scope_sha256="a" * 64,
    )
    assert not result["blocked"]
    assert result["action"] == "rebase_stale_legacy_scalar_to_fresh_exact_suite"
    assert result["evidence"]["lost_covered_units"] == []
    assert result["evidence"]["gained_covered_units"] == ["block-c"]
    assert result["replacement_observation"]["primary_coverage"] == 47.5481


def test_legacy_baseline_pauses_when_an_exact_coverage_unit_is_lost() -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    result = adapter._revalidate_recovery_baseline(
        legacy_observation={"primary_coverage": 50.0},
        legacy_units={"block-a", "block-b"},
        restored={"primary_coverage": 49.9, "covered_units": ["block-a"]},
        coverage_scope_sha256="a" * 64,
    )
    assert result["blocked"]
    assert result["action"] == "pause_exact_coverage_units_regressed"
    assert result["evidence"]["lost_covered_units"] == ["block-b"]


def test_bounded_remeasurement_accepts_only_when_exact_unit_union_recovers_loss() -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    recovered = adapter._union_exact_remeasurements(
        legacy_units={"spin.go:10", "root.go:2"},
        measurements=[
            {
                "coverage_scope_sha256": "a" * 64,
                "primary_coverage": 47.54,
                "covered_statements": 100,
                "covered_units": ["root.go:2"],
            },
            {
                "coverage_scope_sha256": "a" * 64,
                "primary_coverage": 47.55,
                "covered_statements": 101,
                "covered_units": ["root.go:2", "spin.go:10"],
            },
        ],
    )
    assert recovered["accepted"]
    assert recovered["missing_units_after_union"] == []
    assert recovered["union_coverage"]["aggregation"] == (
        "exact_unit_union_with_best_fresh_scalar"
    )

    missing = adapter._union_exact_remeasurements(
        legacy_units={"spin.go:10", "root.go:2"},
        measurements=[
            {
                "coverage_scope_sha256": "a" * 64,
                "primary_coverage": 47.54,
                "covered_units": ["root.go:2"],
            }
        ],
    )
    assert not missing["accepted"]
    assert missing["missing_units_after_union"] == ["spin.go:10"]


def test_missing_unit_owner_prefers_exact_witness_map_then_semantic_command(tmp_path: Path) -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    repo_root = tmp_path / "repo"
    atomic_write_json(
        repo_root / "witnesses/iteration-0002/case_witnesses.json",
        {"witness_map": {"spin_owner": ["coverage:src/spin.go:10"]}},
    )
    owners = adapter._plausible_coverage_unit_owners(
        repo_root=repo_root,
        missing_units={"src/spin.go:10"},
        candidates=[{"name": "other", "args": ["format"]}],
    )
    assert owners == [
        {
            "case_id": "spin_owner",
            "basis": "exact_case_witness_map",
            "matched_units": '["src/spin.go:10"]',
        }
    ]

    (repo_root / "witnesses/iteration-0002/case_witnesses.json").unlink()
    owners = adapter._plausible_coverage_unit_owners(
        repo_root=repo_root,
        missing_units={"src/spin.go:10"},
        candidates=[{"name": "spinner", "area": "interactive", "args": ["spin"]}],
    )
    assert owners[0]["case_id"] == "spinner"
    assert owners[0]["basis"].startswith("semantic_")


def test_raw_fuse_stages_unique_rows_in_reservoir_without_mutating_suite(tmp_path: Path) -> None:
    from v4.adapters import gofumpt_pilot_adapter as adapter

    repo_root = tmp_path / "repo"
    (repo_root / "agent_cases").mkdir(parents=True)
    accepted = [{"name": "accepted", "args": ["accepted"]}]
    (repo_root / "candidates").mkdir()
    (repo_root / "candidates/current.json").write_text(
        json.dumps({"cases": accepted}), encoding="utf-8"
    )
    incoming = [{"name": f"new-{index}", "args": [str(index)]} for index in range(3)]
    (repo_root / "agent_cases/tranche-0002.json").write_text(
        json.dumps({"cases": incoming}), encoding="utf-8"
    )
    response = adapter.static_select(
        {
            "iteration": 2,
            "workflow_context": {
                "recommended_tranche_size": 8,
                "raw_candidate_fuse": 2,
            },
            "repository": {"pilot_raw_fuse": 20},
        },
        repo_root,
    )
    assert response["raw_fuse_crossed"]
    assert response["candidate_state_committed"] is False
    assert response["unique_persisted_raw_candidates"] == 2
    assert response["raw_overflow_count"] == 2
    assert json.loads((repo_root / "candidates/current.json").read_text())["cases"] == accepted
    assert (repo_root / "candidates/iteration-0002.raw_fuse_reservoir.json").is_file()
    ledger = json.loads((repo_root / "artifacts/raw_candidate_ledger.json").read_text())
    assert ledger["unique_persisted_raw_candidates"] == 2
    assert ledger["total_generation_attempts"] == 3
    overflow = json.loads(
        (repo_root / "artifacts/raw_candidate_overflow/iteration-0002.json").read_text()
    )
    assert overflow["overflow_unique_candidates"] == 2
    assert len(overflow["cases"]) == 2


def test_container_contract_is_hardened_and_uses_valid_mount_syntax(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    command = ContainerContract(
        image_id="sha256:" + "a" * 64,
        stage="full_coverage",
        name="pb-v4-test",
        mounts=(Mount(source, "/source", readonly=True),),
    ).docker_run(["/bin/true"])
    joined = " ".join(command)
    assert "--network none" in joined
    assert "--read-only" in command
    assert "--cap-drop ALL" in joined
    assert "no-new-privileges" in command
    assert "--entrypoint" in command
    assert "docker.sock" not in joined
    assert ",readonly" in joined
    assert ",rw" not in joined
    assert "/workspace:rw,exec" in joined
    assert "/home/agent:rw,exec,nosuid,nodev,uid=1000,gid=1000,mode=0700" in joined


def test_execution_provenance_fails_closed() -> None:
    provenance = {
        "stage": "full_coverage",
        "image_id": "tag:latest",
        "network": "bridge",
        "security": {},
    }
    errors = validate_execution_provenance(provenance, expected_stage="full_coverage")
    assert len(errors) >= 7


def test_pb_generation_scope_holds_official_tests_out() -> None:
    valid = {
        "pb_official_tests_visible": False,
        "source_visible_to_generation_agent": True,
        "native_tests_visible_to_generation_agent": True,
        "inference_image_binary_only": True,
        "credentials_mounted_into_container": False,
    }
    assert validate_pb_generation_scope(valid) == []
    invalid = {**valid, "pb_official_tests_visible": True}
    assert validate_pb_generation_scope(invalid)


def test_source_tree_hash_detects_tamper_and_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.go").write_text("package main\n", encoding="utf-8")
    expected = source_tree_sha256(source)
    repo = {
        "commit": "a" * 40,
        "source_dir": str(source),
        "source_tree_sha256": expected,
        "runtime_image_id": "sha256:" + "b" * 64,
    }
    assert validate_repository_scope(repo)["source_tree_sha256"] == expected
    (source / "main.go").write_text("package changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_repository_scope(repo)
    if os.name != "nt":
        os.symlink("main.go", source / "link")
        with pytest.raises(ValueError, match="symlink"):
            source_tree_sha256(source)
