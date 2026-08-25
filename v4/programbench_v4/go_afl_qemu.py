"""Stable, report-only Go first-party AFL++/QEMU metrics for V4.

Go binaries include a large runtime, so whole-ELF QEMU tuples are not a useful
first-party signal.  V4 therefore reuses the audited V3 runner's positive
``.gopclntab``/symbol range whitelist and accepts formal metrics only after
three aligned replays satisfy the stability gate.  These metrics are reporting
evidence only: they are never copied into ``Observation.auxiliary_path_*`` and
cannot affect refinement or stopping.
"""

from __future__ import annotations

import hashlib
import json
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

from .io import atomic_write_json, sha256_json
from .rust_afl_qemu import RustAflQemuError, sha256_file, sha256_path


SCHEMA = "programbench_v4_go_first_party_afl_qemu_metrics_v1"
CHECKPOINT_SCHEMA = "programbench_v4_go_first_party_afl_qemu_checkpoint_v1"
RESULT_SCHEMA = "programbench_afl_qemu_suite_v2"


class GoAflQemuError(RustAflQemuError):
    """Raised when stable Go first-party AFL evidence cannot be established."""


def validate_go_qemu_scope(
    repository: Mapping[str, Any], *, executable: str | Path,
    executable_sha256: str, suite: str | Path, scope_sha256: str,
    runtime_image_id: str, network: str = "none",
) -> dict[str, Any]:
    language = str(repository.get("language") or "").lower()
    if language != "go":
        raise GoAflQemuError("Go-aware AFL/QEMU scope requires language=go")
    commit = str(repository.get("commit") or "")
    if len(commit) != 40 or any(ch not in "0123456789abcdefABCDEF" for ch in commit):
        raise GoAflQemuError("Go repository commit must be a pinned 40-hex commit")
    if network != "none":
        raise GoAflQemuError("Go AFL/QEMU execution must use network=none")
    binary = Path(executable).resolve()
    suite_path = Path(suite).resolve()
    if not binary.is_file():
        raise GoAflQemuError(f"source-built executable is missing: {binary}")
    if not (suite_path.is_file() or suite_path.is_dir()):
        raise GoAflQemuError(f"oracle suite is missing: {suite_path}")
    actual_binary = sha256_file(binary)
    if actual_binary != str(executable_sha256).lower():
        raise GoAflQemuError("source-built executable digest does not match")
    if not str(runtime_image_id).startswith("sha256:"):
        raise GoAflQemuError("runtime image must be content-addressed")
    suite_digest = sha256_path(suite_path)
    identity = {
        "schema": SCHEMA,
        "campaign_scope_sha256": str(scope_sha256),
        "repository_instance_id": str(repository.get("instance_id") or ""),
        "commit": commit.lower(),
        "source_tree_sha256": str(repository.get("source_tree_sha256") or ""),
        "runtime_image_id": str(runtime_image_id),
        "source_built_binary_sha256": actual_binary,
        "suite_sha256": suite_digest,
        "instrumentation_scope": "go_target_module_and_main_function_ranges",
        "network": network,
    }
    return {**identity, "qemu_scope_sha256": sha256_json(identity),
            "language": "go", "source_built_binary": str(binary),
            "suite": str(suite_path)}


def build_v3_go_qemu_command(*, runner: str | Path, python: str, label: str,
                              suite: str | Path, executable: str | Path,
                              output: str | Path, afl_root: str | Path,
                              sample_calls: int | None = None) -> list[str]:
    command = [str(python), str(Path(runner).resolve()), "--label", label,
               "--suite", str(Path(suite).resolve()), "--executable",
               str(Path(executable).resolve()), "--output", str(Path(output).resolve()),
               "--afl-root", str(Path(afl_root).resolve()), "--timeout-cap", "30",
               "--go-first-party-only"]
    if sample_calls is not None:
        if sample_calls <= 0:
            raise GoAflQemuError("sample_calls must be positive")
        command += ["--sample-calls", str(sample_calls), "--sample-tests", str(sample_calls)]
    return command


def _map(path: Path) -> frozenset[int]:
    return frozenset(int(line.split(":", 1)[0]) for line in
                     path.read_text(encoding="ascii").splitlines() if ":" in line)


def _jaccard(left: frozenset[int], right: frozenset[int]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def build_go_qemu_metrics(repeat_outputs: Sequence[str | Path], *,
                          scope: Mapping[str, Any]) -> dict[str, Any]:
    if len(repeat_outputs) < 3:
        raise GoAflQemuError("Go formal AFL metrics require at least three repeats")
    repeats: list[list[frozenset[int]]] = []
    result_digests: list[str] = []
    for raw in repeat_outputs:
        root = Path(raw).resolve()
        result_path = root / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("schema") != RESULT_SCHEMA:
            raise GoAflQemuError("incompatible V3 AFL result schema")
        instrumentation = result.get("instrumentation_scope") or {}
        if instrumentation.get("go_first_party_only") is not True or not instrumentation.get("qemu_inst_ranges"):
            raise GoAflQemuError("Go result lacks a non-empty first-party QEMU range whitelist")
        selected = (result.get("call_sampling") or {}).get("selected_pytest_nodes") or []
        failed = set((result.get("oracle_passing_metrics") or {}).get("failed_nodes") or [])
        maps = sorted((root / "maps").glob("*.map"))
        if not selected or len(selected) != len(maps):
            raise GoAflQemuError("Go node/call alignment is unavailable")
        passing = [_map(path) for path, node in zip(maps, selected) if node not in failed]
        if not passing:
            raise GoAflQemuError("Go repeat has no oracle-passing aligned calls")
        repeats.append(passing)
        result_digests.append(sha256_file(result_path))
    counts = [len(run) for run in repeats]
    if len(set(counts)) != 1:
        raise GoAflQemuError(f"Go repeat call-count mismatch: {counts}")
    stable_calls = [frozenset.intersection(*(run[i] for run in repeats)) for i in range(counts[0])]
    raw_unions = [frozenset().union(*run) for run in repeats]
    stable_union = frozenset().union(*stable_calls)
    pairwise = [_jaccard(repeats[a][i], repeats[b][i])
                for i in range(counts[0]) for a, b in combinations(range(len(repeats)), 2)]
    mean_jaccard = sum(pairwise) / len(pairwise) if pairwise else 1.0
    retention = [100.0 * len(stable_union) / len(union) if union else 100.0 for union in raw_unions]
    accepted = mean_jaccard >= 0.98 and min(retention) >= 95.0
    return {
        "schema": SCHEMA,
        "report_only": True,
        "stop_policy": "never_used_for_refinement_or_stopping",
        "calls": counts[0],
        "distinct_path_signatures": len(set(stable_calls)),
        "absolute_tuple_union": len(stable_union),
        "zero_novelty_calls": sum(not (value - frozenset().union(*stable_calls[:i])) for i, value in enumerate(stable_calls)),
        "stability": {"accepted": accepted, "repeat_count": len(repeats),
                      "mean_per_call_pairwise_jaccard": round(mean_jaccard, 6),
                      "stable_edge_retention_percent": [round(value, 2) for value in retention]},
        "scope": dict(scope), "repeat_result_sha256": result_digests,
    }


def persist_go_qemu_checkpoint(path: str | Path, *, metrics: Mapping[str, Any],
                               iteration: int) -> Path:
    if metrics.get("schema") != SCHEMA:
        raise GoAflQemuError("invalid Go AFL metrics schema")
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, {"schema": CHECKPOINT_SCHEMA, "iteration": int(iteration),
                               "metrics": dict(metrics), "report_only": True})
    return target
