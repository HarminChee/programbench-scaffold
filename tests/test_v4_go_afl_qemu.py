from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from v4.programbench_v4.go_afl_qemu import (
    GoAflQemuError,
    build_go_qemu_metrics,
    build_v3_go_qemu_command,
    persist_go_qemu_checkpoint,
    validate_go_qemu_scope,
)


def test_go_command_requires_first_party_ranges_and_aligned_sampling(tmp_path: Path) -> None:
    command = build_v3_go_qemu_command(
        runner=tmp_path / "runner.py", python="python3", label="go-demo",
        suite=tmp_path / "suite", executable=tmp_path / "binary",
        output=tmp_path / "out", afl_root=tmp_path / "afl", sample_calls=50,
    )
    assert "--go-first-party-only" in command
    assert command[-4:] == ["--sample-calls", "50", "--sample-tests", "50"]
    assert "--edge-only" not in command


def _repeat(root: Path, rows: list[set[int]]) -> Path:
    maps = root / "maps"
    maps.mkdir(parents=True)
    nodes = []
    for index, values in enumerate(rows, 1):
        (maps / f"{index:08d}.map").write_text(
            "".join(f"{value}:1\n" for value in sorted(values)), encoding="ascii"
        )
        nodes.append(f"tests/test_generated_cli_oracle.py::test_{index}")
    (root / "result.json").write_text(json.dumps({
        "schema": "programbench_afl_qemu_suite_v2",
        "instrumentation_scope": {"go_first_party_only": True,
                                  "qemu_inst_ranges": "0x100-0x200"},
        "call_sampling": {"selected_pytest_nodes": nodes},
        "oracle_passing_metrics": {"failed_nodes": []},
    }), encoding="utf-8")
    return root


def test_go_metrics_use_n3_stable_intersection_and_are_report_only(tmp_path: Path) -> None:
    repeats = [_repeat(tmp_path / f"r{i}", [{1, 2}, {2, 3}]) for i in range(3)]
    metrics = build_go_qemu_metrics(repeats, scope={"qemu_scope_sha256": "a" * 64})
    assert metrics["calls"] == 2
    assert metrics["distinct_path_signatures"] == 2
    assert metrics["absolute_tuple_union"] == 3
    assert metrics["stability"]["accepted"] is True
    assert metrics["stop_policy"] == "never_used_for_refinement_or_stopping"
    target = persist_go_qemu_checkpoint(tmp_path / "checkpoint.json", metrics=metrics, iteration=2)
    assert json.loads(target.read_text())["report_only"] is True


def test_go_metrics_reject_unaligned_or_unstable_evidence(tmp_path: Path) -> None:
    with pytest.raises(GoAflQemuError, match="at least three"):
        build_go_qemu_metrics([], scope={})
    repeats = [_repeat(tmp_path / f"r{i}", [{1}, {2}]) for i in range(3)]
    result = json.loads((repeats[0] / "result.json").read_text())
    result["call_sampling"]["selected_pytest_nodes"] = []
    (repeats[0] / "result.json").write_text(json.dumps(result))
    with pytest.raises(GoAflQemuError, match="alignment"):
        build_go_qemu_metrics(repeats, scope={})


def test_go_scope_binds_binary_suite_and_offline_image(tmp_path: Path) -> None:
    binary = tmp_path / "binary"; binary.write_bytes(b"go")
    suite = tmp_path / "suite"; suite.mkdir(); (suite / "x").write_text("x")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    scope = validate_go_qemu_scope(
        {"instance_id": "go", "language": "go", "commit": "a" * 40,
         "source_tree_sha256": "b" * 64}, executable=binary,
        executable_sha256=digest, suite=suite, scope_sha256="c" * 64,
        runtime_image_id="sha256:" + "d" * 64,
    )
    assert scope["instrumentation_scope"] == "go_target_module_and_main_function_ranges"
