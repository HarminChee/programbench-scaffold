from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from v4.programbench_v4.rust_afl_qemu import (
    CHECKPOINT_SCHEMA,
    RustAflQemuError,
    build_rust_qemu_checkpoint,
    build_rust_qemu_metrics,
    build_v3_rust_qemu_command,
    persist_rust_qemu_checkpoint,
    rust_qemu_path_marginal_signal,
    validate_rust_qemu_scope,
)


def _repo() -> dict[str, str]:
    return {
        "instance_id": "demo-rust",
        "language": "rust",
        "commit": "a" * 40,
        "source_tree_sha256": "b" * 64,
    }


def _inputs(tmp_path: Path) -> tuple[Path, Path, str, str]:
    binary = tmp_path / "reference_executable"
    binary.write_bytes(b"source-built-rust-binary")
    suite = tmp_path / "suite.tar.gz"
    suite.write_bytes(b"offline-oracle-suite")
    return binary, suite, hashlib.sha256(binary.read_bytes()).hexdigest(), hashlib.sha256(suite.read_bytes()).hexdigest()


def test_rust_scope_requires_fixed_source_binary_and_offline_container(tmp_path: Path) -> None:
    binary, suite, binary_sha, suite_sha = _inputs(tmp_path)
    scope = validate_rust_qemu_scope(
        _repo(),
        executable=binary,
        executable_sha256=binary_sha,
        suite=suite,
        suite_sha256=suite_sha,
        scope_sha256="c" * 64,
        runtime_image_id="sha256:" + "d" * 64,
    )
    assert scope["language"] == "rust"
    assert scope["source_built_binary_sha256"] == binary_sha
    assert scope["network"] == "none"
    assert len(scope["qemu_scope_sha256"]) == 64

    with pytest.raises(RustAflQemuError, match="requires Rust, C, or C\\+\\+"):
        validate_rust_qemu_scope(
            {**_repo(), "language": "go"},
            executable=binary,
            executable_sha256=binary_sha,
            suite=suite,
            suite_sha256=suite_sha,
            scope_sha256="c" * 64,
            runtime_image_id="sha256:" + "d" * 64,
        )
    c_scope = validate_rust_qemu_scope(
        {**_repo(), "language": "c"}, executable=binary,
        executable_sha256=binary_sha, suite=suite, suite_sha256=suite_sha,
        scope_sha256="c" * 64, runtime_image_id="sha256:" + "d" * 64,
    )
    assert c_scope["language"] == "c"
    with pytest.raises(RustAflQemuError, match="network=none"):
        validate_rust_qemu_scope(
            _repo(),
            executable=binary,
            executable_sha256=binary_sha,
            suite=suite,
            suite_sha256=suite_sha,
            scope_sha256="c" * 64,
            runtime_image_id="sha256:" + "d" * 64,
            network="bridge",
        )


def test_v3_command_preserves_bucketed_path_semantics(tmp_path: Path) -> None:
    command = build_v3_rust_qemu_command(
        runner=tmp_path / "run_afl_qemu_suite.py",
        python="/usr/bin/python3",
        label="rust-demo",
        suite=tmp_path / "suite.tar.gz",
        executable=tmp_path / "reference_executable",
        output=tmp_path / "out",
        afl_root=tmp_path / "afl++",
        sample_calls=20,
        sample_tests=20,
    )
    assert "--edge-only" not in command
    assert "--go-first-party-only" not in command
    assert command[0] == "/usr/bin/python3"
    assert "run_afl_qemu_suite.py" in command[1]
    assert command[-4:] == ["--sample-calls", "20", "--sample-tests", "20"]


def test_metrics_map_v3_path_to_distinct_signatures_and_edge_to_union(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result = {
        "schema": "programbench_afl_qemu_suite_v2",
        "backend": {
            "edge_only": False,
            "map_semantics": "AFL tuple bitmap with classified hit-count buckets",
        },
        "binary_calls": 12,
        "distinct_path_signatures": 7,
        "absolute_tuple_union": 31,
    }
    result_path.write_text(json.dumps(result), encoding="utf-8")
    scope = {
        "language": "rust",
        "qemu_scope_sha256": "e" * 64,
        "campaign_scope_sha256": "c" * 64,
        "source_built_binary_sha256": "d" * 64,
        "runtime_image_id": "sha256:" + "f" * 64,
        "suite_sha256": "1" * 64,
        "network": "none",
    }
    metrics = build_rust_qemu_metrics(result, scope=scope, result_path=result_path)
    assert metrics["path"] == {
        "name": "distinct_path_signatures",
        "value": 7,
    }
    assert metrics["edge"] == {
        "name": "absolute_tuple_union",
        "value": 31,
        "report_only": True,
    }
    assert metrics["metric_semantics"]["edge_stop_policy"] == "report_only"

    result["backend"]["edge_only"] = True
    with pytest.raises(RustAflQemuError, match="classified hit-count"):
        build_rust_qemu_metrics(result, scope=scope, result_path=result_path)


def test_two_sub_one_percent_path_gains_are_only_an_auxiliary_hint() -> None:
    signal = rust_qemu_path_marginal_signal([100, 100, 100])
    assert signal["relative_gains"] == [0.0, 0.0]
    assert signal["low_marginal_auxiliary"] is True
    assert signal["eligible_for_auxiliary_stop_hint"] is True
    assert signal["edge_used_for_stop"] is False

    growing = rust_qemu_path_marginal_signal([100, 100, 102])
    assert growing["low_marginal_auxiliary"] is False
    assert growing["relative_gains"][-1] == pytest.approx(0.02)

    unstable = rust_qemu_path_marginal_signal([100, 99, 98])
    assert unstable["non_monotonic"] is True
    assert unstable["eligible_for_auxiliary_stop_hint"] is False


def test_qemu_checkpoint_is_scoped_and_atomic(tmp_path: Path) -> None:
    scope = {
        "qemu_scope_sha256": "e" * 64,
        "campaign_scope_sha256": "c" * 64,
        "source_built_binary_sha256": "d" * 64,
        "runtime_image_id": "sha256:" + "f" * 64,
        "suite_sha256": "1" * 64,
    }
    metrics = {
        "schema": "programbench_v4_rust_afl_qemu_metrics_v1",
        "path": {"name": "distinct_path_signatures", "value": 10},
        "edge": {"name": "absolute_tuple_union", "value": 44, "report_only": True},
        "provenance": {"network": "none", "containerized": True},
    }
    checkpoint = build_rust_qemu_checkpoint(
        metrics=metrics, scope=scope, iteration=3, path_history=[10, 10, 10]
    )
    assert checkpoint["schema"] == CHECKPOINT_SCHEMA
    assert checkpoint["path_marginal_auxiliary"]["low_marginal_auxiliary"] is True
    assert checkpoint["edge_report_only"] is True
    target = persist_rust_qemu_checkpoint(tmp_path / "stages" / "checkpoint.json", checkpoint)
    assert json.loads(target.read_text(encoding="utf-8"))["schema"] == CHECKPOINT_SCHEMA


def test_capture_wrapper_does_not_depend_on_target_path() -> None:
    wrapper = Path("v3/tools/afl_qemu_capture_wrapper.sh").read_text(encoding="utf-8")
    assert wrapper.startswith("#!/bin/bash\n")
    assert "/bin/mkdir" in wrapper
    assert "/usr/bin/flock" in wrapper
    assert "#!/usr/bin/env bash" not in wrapper
