"""V4's native-binary AFL++/QEMU path-and-edge evidence bridge.

This module deliberately does not replace Rust's LLVM region coverage.  It
adapts the already audited V3 ``run_afl_qemu_suite.py`` runner for a pinned,
source-built Rust executable and records the two dynamic signals with explicit
semantics:

* ``distinct_path_signatures`` is the number of distinct complete classified
  bitmap signatures, one signature per captured binary call;
* ``absolute_tuple_union`` is the union of tuple IDs observed by those calls.

The bridge covers Rust, C and C++ source-built binaries, is offline/container-
scoped, and is disabled unless a repository explicitly opts in. The path signal can be used as an auxiliary
marginal-saturation hint after two consecutive relative gains below 1%; the
edge signal is report-only and never participates in stopping.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from .io import atomic_write_json, sha256_json


SCHEMA = "programbench_v4_rust_afl_qemu_metrics_v1"
CHECKPOINT_SCHEMA = "programbench_v4_rust_afl_qemu_checkpoint_v1"
V3_RESULT_SCHEMA = "programbench_afl_qemu_suite_v2"
PATH_METRIC = "distinct_path_signatures"
EDGE_METRIC = "absolute_tuple_union"
RELATIVE_LOW_THRESHOLD = 0.01
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class RustAflQemuError(ValueError):
    """Raised when the opt-in bridge cannot prove its execution scope."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_path(path: Path) -> str:
    """Hash a file or a directory with stable relative-path framing."""

    if path.is_file():
        return sha256_file(path)
    if not path.is_dir():
        raise RustAflQemuError(f"path is neither a file nor directory: {path}")
    digest = hashlib.sha256()
    for child in sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: item.relative_to(path).as_posix()):
        relative = child.relative_to(path).as_posix().encode("utf-8")
        payload = child.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _require_path(value: str | Path, label: str) -> Path:
    path = Path(value).resolve()
    if not path.is_file() and not path.is_dir():
        raise RustAflQemuError(f"{label} is not a regular file or directory: {path}")
    return path


def _require_file(value: str | Path, label: str) -> Path:
    path = Path(value).resolve()
    if not path.is_file():
        raise RustAflQemuError(f"{label} is not a regular file: {path}")
    return path


def _require_sha256(value: object, label: str) -> str:
    result = str(value or "").lower()
    if not _SHA256_RE.fullmatch(result):
        raise RustAflQemuError(f"{label} must be a lowercase SHA-256 digest")
    return result


def validate_rust_qemu_scope(
    repository: Mapping[str, Any],
    *,
    executable: str | Path,
    executable_sha256: str,
    suite: str | Path,
    suite_sha256: str | None,
    scope_sha256: str,
    runtime_image_id: str,
    network: str = "none",
    containerized: bool = True,
    source_built: bool = True,
) -> dict[str, Any]:
    """Validate immutable inputs before constructing a V3 runner command.

    ``executable`` must be the source-built reference binary, not an LLVM
    instrumented coverage binary.  The caller/container is responsible for
    enforcing the immutable runtime image and network policy; this function
    records and checks those claims so a receipt cannot silently look like an
    offline run.
    """

    language = str(repository.get("language") or "").lower()
    if language not in {"rust", "c", "cpp", "c++"}:
        raise RustAflQemuError("native AFL++ QEMU evidence requires Rust, C, or C++")
    if not _COMMIT_RE.fullmatch(str(repository.get("commit") or "")):
        raise RustAflQemuError("Rust repository commit must be a pinned 40-hex commit")
    if network != "none":
        raise RustAflQemuError("Rust AFL++ QEMU execution must use network=none")
    if not containerized:
        raise RustAflQemuError("Rust AFL++ QEMU execution must be containerized")
    if not source_built:
        raise RustAflQemuError("Rust AFL++ QEMU requires a source-built binary")
    if not str(runtime_image_id).startswith("sha256:") or not _SHA256_RE.fullmatch(
        str(runtime_image_id)[7:]
    ):
        raise RustAflQemuError("runtime_image_id must be an immutable sha256 image ID")
    if not _SHA256_RE.fullmatch(str(scope_sha256 or "")):
        raise RustAflQemuError("scope_sha256 must be a SHA-256 digest")

    binary = _require_file(executable, "source-built executable")
    actual_binary_sha256 = sha256_file(binary)
    expected_binary_sha256 = _require_sha256(executable_sha256, "executable_sha256")
    if actual_binary_sha256 != expected_binary_sha256:
        raise RustAflQemuError("source-built executable digest does not match the request")
    suite_path = _require_path(suite, "oracle suite")
    actual_suite_sha256 = sha256_path(suite_path)
    expected_suite_sha256 = (
        actual_suite_sha256
        if suite_sha256 is None
        else _require_sha256(suite_sha256, "suite_sha256")
    )
    if actual_suite_sha256 != expected_suite_sha256:
        raise RustAflQemuError("oracle suite digest does not match the request")

    qemu_scope = sha256_json(
        {
            "schema": SCHEMA,
            "campaign_scope_sha256": str(scope_sha256),
            "repository": str(repository.get("instance_id") or ""),
            "commit": str(repository["commit"]),
            "source_tree_sha256": str(repository.get("source_tree_sha256") or ""),
            "runtime_image_id": str(runtime_image_id),
            "source_built_binary_sha256": actual_binary_sha256,
            "suite_sha256": actual_suite_sha256,
            "network": network,
        }
    )
    return {
        "schema": "programbench_v4_rust_afl_qemu_scope_v1",
        "campaign_scope_sha256": str(scope_sha256),
        "qemu_scope_sha256": qemu_scope,
        "repository_instance_id": str(repository.get("instance_id") or ""),
        "language": "cpp" if language == "c++" else language,
        "commit": str(repository["commit"]),
        "source_tree_sha256": str(repository.get("source_tree_sha256") or ""),
        "runtime_image_id": str(runtime_image_id),
        "source_built_binary": str(binary),
        "source_built_binary_sha256": actual_binary_sha256,
        "suite": str(suite_path),
        "suite_sha256": actual_suite_sha256,
        "network": network,
        "containerized": True,
        "source_built": True,
    }


def build_v3_rust_qemu_command(
    *,
    runner: str | Path,
    python: str,
    label: str,
    suite: str | Path,
    executable: str | Path,
    output: str | Path,
    afl_root: str | Path,
    timeout_cap: float = 30.0,
    sample_calls: int | None = None,
    sample_tests: int | None = None,
) -> list[str]:
    """Build the audited V3 runner invocation for a Rust binary.

    We intentionally omit ``--edge-only``: path signatures need complete
    classified bitmaps (including hit-count buckets).  The same result's tuple
    IDs provide the report-only edge union.
    """

    if timeout_cap <= 0:
        raise RustAflQemuError("timeout_cap must be positive")
    command = [
        str(python),
        str(Path(runner).resolve()),
        "--label",
        str(label),
        "--suite",
        str(Path(suite).resolve()),
        "--executable",
        str(Path(executable).resolve()),
        "--output",
        str(Path(output).resolve()),
        "--afl-root",
        str(Path(afl_root).resolve()),
        "--timeout-cap",
        str(timeout_cap),
    ]
    if sample_calls is not None:
        if sample_calls <= 0:
            raise RustAflQemuError("sample_calls must be positive")
        command.extend(["--sample-calls", str(sample_calls)])
    if sample_tests is not None:
        if sample_tests <= 0:
            raise RustAflQemuError("sample_tests must be positive")
        command.extend(["--sample-tests", str(sample_tests)])
    return command


def _result_metrics(result: Mapping[str, Any]) -> tuple[int, int]:
    if result.get("schema") != V3_RESULT_SCHEMA:
        raise RustAflQemuError("QEMU result is not the audited V3 suite schema")
    backend = result.get("backend") or {}
    if backend.get("edge_only") is not False:
        raise RustAflQemuError("QEMU path metrics require classified hit-count buckets")
    if backend.get("map_semantics") != "AFL tuple bitmap with classified hit-count buckets":
        raise RustAflQemuError("QEMU result has incompatible bitmap semantics")
    try:
        path_count = int(result[PATH_METRIC])
        edge_count = int(result[EDGE_METRIC])
    except (KeyError, TypeError, ValueError) as exc:
        raise RustAflQemuError("QEMU result lacks integer path/edge metrics") from exc
    if path_count < 0 or edge_count < 0:
        raise RustAflQemuError("QEMU path/edge metrics cannot be negative")
    binary_calls = int(result.get("binary_calls") or 0)
    if binary_calls <= 0:
        raise RustAflQemuError("QEMU result has no captured binary calls")
    if binary_calls < path_count:
        raise RustAflQemuError("distinct path count exceeds captured binary calls")
    return path_count, edge_count


def build_rust_qemu_metrics(
    result: Mapping[str, Any], *, scope: Mapping[str, Any], result_path: str | Path
) -> dict[str, Any]:
    """Convert a V3 result into a scoped V4 report-only metrics record."""

    path_count, edge_count = _result_metrics(result)
    return {
        "schema": SCHEMA,
        "metric_semantics": {
            "path": PATH_METRIC,
            "path_definition": "distinct complete classified per-call bitmap signatures",
            "edge": EDGE_METRIC,
            "edge_definition": "absolute union of tuple IDs across captured calls",
            "edge_stop_policy": "report_only",
        },
        "path": {"name": PATH_METRIC, "value": path_count},
        "edge": {"name": EDGE_METRIC, "value": edge_count, "report_only": True},
        "binary_calls": int(result.get("binary_calls") or 0),
        "result_path": str(Path(result_path).resolve()),
        "result_sha256": sha256_file(Path(result_path)) if Path(result_path).is_file() else None,
        "scope": dict(scope),
        "provenance": {
            "runner": "v3/tools/run_afl_qemu_suite.py",
            "v3_result_schema": V3_RESULT_SCHEMA,
            "network": "none",
            "containerized": True,
            "source_built_binary_sha256": scope.get("source_built_binary_sha256"),
            "runtime_image_id": scope.get("runtime_image_id"),
        },
    }


def rust_qemu_path_marginal_signal(path_values: Sequence[int | float]) -> dict[str, Any]:
    """Return a path-only auxiliary low-marginal signal.

    Two consecutive relative improvements below 1% are sufficient for the
    auxiliary signal.  This never produces a terminal decision and does not
    inspect the edge union.
    """

    values = [max(0.0, float(value)) for value in path_values]
    gains: list[float] = []
    for previous, current in zip(values, values[1:]):
        gains.append((current - previous) / max(previous, 1.0))
    recent = gains[-2:]
    non_monotonic = any(gain < 0.0 for gain in recent)
    low = (
        len(recent) == 2
        and not non_monotonic
        and all(gain < RELATIVE_LOW_THRESHOLD for gain in recent)
    )
    return {
        "metric": PATH_METRIC,
        "values": [int(value) if value.is_integer() else value for value in values],
        "relative_gains": recent,
        "relative_low_threshold": RELATIVE_LOW_THRESHOLD,
        "consecutive_low_rounds": 2 if low else len(recent),
        "low_marginal_auxiliary": low,
        "eligible_for_auxiliary_stop_hint": low,
        "non_monotonic": non_monotonic,
        "edge_used_for_stop": False,
    }


def build_rust_qemu_checkpoint(
    *,
    metrics: Mapping[str, Any],
    scope: Mapping[str, Any],
    iteration: int,
    path_history: Sequence[int | float],
) -> dict[str, Any]:
    """Build a durable checkpoint payload without changing V4 acceptance state."""

    if int(iteration) < 1:
        raise RustAflQemuError("QEMU checkpoint iteration must be positive")
    if metrics.get("schema") != SCHEMA:
        raise RustAflQemuError("invalid Rust QEMU metrics schema")
    signal = rust_qemu_path_marginal_signal(path_history)
    return {
        "schema": CHECKPOINT_SCHEMA,
        "iteration": int(iteration),
        "qemu_scope_sha256": scope.get("qemu_scope_sha256"),
        "campaign_scope_sha256": scope.get("campaign_scope_sha256"),
        "source_built_binary_sha256": scope.get("source_built_binary_sha256"),
        "runtime_image_id": scope.get("runtime_image_id"),
        "suite_sha256": scope.get("suite_sha256"),
        "metrics": dict(metrics),
        "path_marginal_auxiliary": signal,
        "edge_report_only": True,
        "provenance": metrics.get("provenance"),
    }


def persist_rust_qemu_checkpoint(path: str | Path, checkpoint: Mapping[str, Any]) -> Path:
    """Atomically publish a scoped QEMU checkpoint below the stage output."""

    target = Path(path).resolve()
    if checkpoint.get("schema") != CHECKPOINT_SCHEMA:
        raise RustAflQemuError("invalid Rust QEMU checkpoint schema")
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, dict(checkpoint))
    return target


def run_v3_rust_qemu_suite(
    *,
    command: Sequence[str],
    scope: Mapping[str, Any],
    output: str | Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run the V3 runner under the caller's already-created offline container.

    This function only launches a supplied command; it never creates a Docker
    container or changes campaign state.  The caller must provide the offline,
    immutable-image container boundary and pass its command/paths accordingly.
    """

    if scope.get("language") not in {"rust", "c", "cpp"} or scope.get("network") != "none":
        raise RustAflQemuError("run scope is not a supported native offline scope")
    completed = subprocess.run(list(command), capture_output=True, text=True, env=dict(env or os.environ))
    result_path = Path(output).resolve() / "result.json"
    if completed.returncode != 0 or not result_path.is_file():
        raise RustAflQemuError(
            f"V3 AFL/QEMU runner failed rc={completed.returncode}; result={result_path.is_file()}"
        )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    metrics = build_rust_qemu_metrics(result, scope=scope, result_path=result_path)
    metrics["runner_returncode"] = completed.returncode
    metrics["runner_stdout_tail"] = completed.stdout[-2000:]
    metrics["runner_stderr_tail"] = completed.stderr[-2000:]
    return metrics
