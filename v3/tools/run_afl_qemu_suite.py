#!/usr/bin/env python3
"""Replay a generated oracle bundle through AFL++ QEMU mode.

The bundle's own pytest runtime remains the semantic authority.  A transparent
executable wrapper records one AFL classified bitmap for every real binary call.
The default bitmap retains AFL's coarse edge hit-count buckets and is therefore
usable as an execution-path signature. Results retain every per-call map.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "v3" / "tools" / "afl_qemu_capture_wrapper.sh"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_map(path: Path) -> dict[int, int]:
    result: dict[int, int] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        if not line:
            continue
        tuple_id, value = line.split(":", 1)
        result[int(tuple_id)] = int(value)
    return result


def map_signature(values: dict[int, int]) -> str:
    """Hash the complete classified AFL bitmap, independent of text layout."""
    normalized = "".join(f"{tuple_id}:{values[tuple_id]}\n" for tuple_id in sorted(values))
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def go_first_party_inst_ranges(executable: Path) -> tuple[str | None, str | None]:
    """Return merged Go target-module/main text ranges for qemuafl filtering."""
    go = shutil.which("go")
    if not go and Path("/usr/local/go1.21.13/bin/go").is_file():
        go = "/usr/local/go1.21.13/bin/go"
    if not go:
        return None, None
    version = subprocess.run([go, "version", "-m", str(executable)], capture_output=True, text=True)
    module = None
    for line in version.stdout.splitlines():
        fields = line.strip().split()
        if len(fields) >= 2 and fields[0] == "path":
            module = fields[1]
            break
    if not module:
        return None, None
    readelf = shutil.which("readelf")
    if readelf:
        header = subprocess.run([readelf, "-h", str(executable)], capture_output=True, text=True)
        if re.search(r"(?m)^\s*Type:\s+DYN\b", header.stdout):
            raise SystemExit(
                "Go first-party QEMU ranges currently require an ET_EXEC binary; "
                "PIE load-bias recovery has not been validated"
            )
    parsed: list[tuple[int, int, str]] = []
    # Prefer Go's own .gopclntab metadata: unlike the ELF symbol table it is
    # normally retained in stripped binaries and carries exact function ends.
    helper = ROOT / "v3" / "tools" / "go_pclntab_ranges.go"
    pclntab = subprocess.run(
        [go, "run", str(helper), str(executable)], capture_output=True, text=True
    )
    if pclntab.returncode == 0:
        for line in pclntab.stdout.splitlines():
            try:
                item = json.loads(line)
                parsed.append((int(item["start"]), int(item["end"]), str(item["name"])))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
    # Fallback for unusual binaries whose pclntab cannot be decoded but which
    # still retain conventional ELF symbols.
    if not parsed:
        nm = subprocess.run([go, "tool", "nm", "-size", str(executable)], capture_output=True, text=True)
        for line in nm.stdout.splitlines():
            fields = line.strip().split(maxsplit=3)
            if len(fields) != 4:
                continue
            try:
                address = int(fields[0], 16)
                size = int(fields[1], 10)
            except ValueError:
                continue
            if fields[2] in {"T", "t"}:
                parsed.append((address, address + size, fields[3]))
    parsed.sort()
    ranges = []
    for start, end, name in parsed:
        if not (name.startswith(module + "/") or name.startswith(module + ".") or name.startswith("main.")):
            continue
        # Package initialization is deterministic setup rather than target
        # behavior and can dominate tiny CLIs.  Runtime/GC/scheduler symbols are
        # already excluded by the positive module/main whitelist.
        if re.search(r"(?:^|[/.])init(?:\.|$)", name):
            continue
        if end > start:
            ranges.append((start, end))
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    # qemuafl compares these ranges with guest virtual PCs. ProgramBench's
    # ordinary statically linked ET_EXEC Go binaries use their ELF symbol
    # addresses directly. A synthetic 0x4000000000 host bias was validated and
    # produced only one constant tuple, so it must not be applied here.
    return module, ",".join(f"0x{start:x}-0x{end:x}" for start, end in merged) or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--suite", type=Path, required=True,
                        help="tar.gz bundle or eval directory")
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", default="/home/programbench/research/programbench-scaffold/.venv/bin/python")
    parser.add_argument("--afl-root", default="/home/programbench/research/tools/afl-src/aflplusplus-4.00c")
    parser.add_argument("--timeout-cap", type=float, default=30.0)
    parser.add_argument("--edge-only", action="store_true",
                        help="discard hit-count buckets (legacy edge-union mode)")
    parser.add_argument("--go-first-party-only", action="store_true",
                        help="instrument only target Go module and main text ranges")
    parser.add_argument("--sample-calls", type=int,
                        help="uniformly capture at most this many expected calls; other calls run natively")
    parser.add_argument("--sample-tests", type=int,
                        help="uniformly execute at most this many collected pytest nodes")
    args = parser.parse_args()

    if not args.executable.is_file():
        raise SystemExit(f"missing executable: {args.executable}")
    args.output.mkdir(parents=True, exist_ok=True)
    eval_root = args.output / "eval"
    maps = args.output / "maps"
    if eval_root.exists():
        shutil.rmtree(eval_root)
    if maps.exists():
        shutil.rmtree(maps)
    eval_root.mkdir()
    maps.mkdir()

    if args.suite.is_file():
        with tarfile.open(args.suite) as archive:
            archive.extractall(eval_root, filter="data")
    else:
        shutil.copytree(args.suite, eval_root, dirs_exist_ok=True)
    # Archives sometimes contain an extra oracle_tests/eval prefix.
    candidates = list(eval_root.rglob("generated_cli_manifest.json"))
    if len(candidates) != 1:
        raise SystemExit(f"expected one manifest, found {len(candidates)}")
    manifest_path = candidates[0]
    actual_eval = manifest_path.parent
    workspace = actual_eval.parent
    executable_link = workspace / "executable"
    executable_link.unlink(missing_ok=True)
    executable_link.symlink_to(WRAPPER)
    manifest = load(manifest_path)

    declared_cases = len(manifest.get("cases") or [])
    capture_indices = None
    if args.sample_calls and declared_cases > args.sample_calls:
        if args.sample_calls == 1:
            indices = [1]
        else:
            indices = sorted({1 + round(i * (declared_cases - 1) / (args.sample_calls - 1))
                              for i in range(args.sample_calls)})
        capture_indices = ",".join(map(str, indices))

    env = os.environ.copy()
    go_module, inst_ranges = (go_first_party_inst_ranges(args.executable.resolve())
                              if args.go_first_party_only else (None, None))
    if args.go_first_party_only and not inst_ranges:
        raise SystemExit(
            "Go first-party instrumentation was requested but no target-module "
            "function ranges could be recovered from .gopclntab or ELF symbols"
        )
    env.update({
        "PROGRAMBENCH_AFL_REAL_EXECUTABLE": str(args.executable.resolve()),
        "PROGRAMBENCH_AFL_MAP_DIR": str(maps.resolve()),
        "PROGRAMBENCH_AFL_ROOT": args.afl_root,
        "PROGRAMBENCH_CASE_TIMEOUT_CAP": str(args.timeout_cap),
        "PROGRAMBENCH_AFL_EDGE_ONLY": "1" if args.edge_only else "0",
        "GOMAXPROCS": "1",
        "GODEBUG": "randautoseed=0",
        "TZ": "UTC",
    })
    if inst_ranges:
        env["PROGRAMBENCH_AFL_INST_RANGES"] = inst_ranges
    if capture_indices:
        env["PROGRAMBENCH_AFL_CAPTURE_INDICES"] = capture_indices
    selected_nodes = None
    if args.sample_tests:
        collected = subprocess.run(
            [args.python, "-m", "pytest", "--collect-only", "-q", "tests/test_generated_cli_oracle.py"],
            cwd=actual_eval, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        nodes = [line.strip() for line in collected.stdout.splitlines()
                 if "::" in line and not line.lstrip().startswith(("=", "<"))]
        if collected.returncode not in (0, 5) or not nodes:
            raise SystemExit(f"pytest collection failed:\n{collected.stdout[-4000:]}")
        if len(nodes) > args.sample_tests:
            selected_nodes = [nodes[round(i * (len(nodes) - 1) / (args.sample_tests - 1))]
                              for i in range(args.sample_tests)] if args.sample_tests > 1 else [nodes[0]]
        else:
            selected_nodes = nodes
    command = [args.python, "-m", "pytest", "-q", *(selected_nodes or ["tests/test_generated_cli_oracle.py"])]
    started = time.perf_counter()
    done = subprocess.run(command, cwd=actual_eval, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    duration = time.perf_counter() - started
    map_paths = sorted(maps.glob("*.map"))
    if args.go_first_party_only and not map_paths:
        raise SystemExit(
            "Go first-party instrumentation produced no AFL maps; verify the "
            "QEMU guest load bias and recovered function ranges"
        )
    per_call = []
    union: set[int] = set()
    signatures: dict[str, int] = {}
    novelty = []
    for path in map_paths:
        bitmap = parse_map(path)
        edges = set(bitmap)
        new = edges - union
        novelty.append(len(new))
        union |= edges
        signature = map_signature(bitmap)
        signatures[signature] = signatures.get(signature, 0) + 1
        per_call.append({"map": path.name, "tuples": len(edges), "new_tuples": len(new),
                         "map_sha256": sha256(path), "path_signature_sha256": signature})
    passed_match = re.search(r"(\d+) passed", done.stdout)
    failed_match = re.search(r"(\d+) failed", done.stdout)
    passed = int(passed_match.group(1)) if passed_match else (len(manifest.get("cases") or []) if done.returncode == 0 else 0)
    failed = int(failed_match.group(1)) if failed_match else 0
    failed_nodes = set(re.findall(r"^FAILED\s+(\S+::\S+)", done.stdout, re.MULTILINE))
    node_call_alignment = bool(selected_nodes) and len(selected_nodes) == len(map_paths)
    passing_map_paths = (
        [path for path, node in zip(map_paths, selected_nodes) if node not in failed_nodes]
        if node_call_alignment else []
    )
    passing_union: set[int] = set()
    passing_signatures: set[str] = set()
    passing_zero_novelty = 0
    for path in passing_map_paths:
        bitmap = parse_map(path)
        edges = set(bitmap)
        if not (edges - passing_union):
            passing_zero_novelty += 1
        passing_union |= edges
        passing_signatures.add(map_signature(bitmap))
    expected_nonzero = sum(int(case.get("returncode") or 0) != 0 for case in (manifest.get("cases") or []))
    exit_status_only = failed > 0 and failed == expected_nonzero and "assert 0 ==" in done.stdout
    result = {
        "schema": "programbench_afl_qemu_suite_v2",
        "label": args.label,
        "backend": {"afl_version": "4.00c", "mode": "QEMU", "edge_only": args.edge_only,
                    "map_semantics": ("edge presence only" if args.edge_only else
                                      "AFL tuple bitmap with classified hit-count buckets"),
                    "path_signature_semantics":
                    "SHA-256 of the complete normalized per-call classified bitmap; not theoretical path coverage"},
        "instrumentation_scope": {"go_first_party_only": args.go_first_party_only,
                                  "go_module": go_module,
                                  "qemu_inst_ranges": inst_ranges},
        "suite": str(args.suite), "suite_sha256": sha256(args.suite) if args.suite.is_file() else None,
        "manifest": str(manifest_path), "manifest_sha256": sha256(manifest_path),
        "declared_cases": declared_cases,
        "call_sampling": {"requested_limit": args.sample_calls,
                          "expected_call_basis": declared_cases,
                          "capture_indices": capture_indices,
                          "requested_test_limit": args.sample_tests,
                          "selected_pytest_nodes": selected_nodes},
        "executable": str(args.executable), "executable_sha256": sha256(args.executable),
        "pytest_returncode": done.returncode, "pytest_output": done.stdout[-20000:],
        "pytest_passed": passed, "pytest_failed": failed,
        "oracle_passing_metrics": {
            "available": node_call_alignment,
            "alignment": "one selected pytest node to one captured binary call" if node_call_alignment else None,
            "calls": len(passing_map_paths) if node_call_alignment else None,
            "distinct_path_signatures": len(passing_signatures) if node_call_alignment else None,
            "absolute_tuple_union": len(passing_union) if node_call_alignment else None,
            "zero_novelty_calls": passing_zero_novelty if node_call_alignment else None,
            "failed_nodes": sorted(failed_nodes) if node_call_alignment else None,
        },
        "expected_nonzero_exit_cases": expected_nonzero,
        "wrapper_exit_status_only_mismatch": exit_status_only,
        "semantic_replay_status": "bitmap_usable_exit_status_transport_limitation" if exit_status_only else ("passed" if done.returncode == 0 else "failed"),
        "duration_seconds": round(duration, 6), "binary_calls": len(map_paths),
        "total_binary_calls": int((maps / ".call_counter").read_text()) if (maps / ".call_counter").is_file() else len(map_paths),
        "valid_path_signature_calls": len(map_paths),
        "distinct_path_signatures": len(signatures),
        "path_signature_diversity_percent": round(100 * len(signatures) / len(map_paths), 4) if map_paths else None,
        "duplicate_path_signature_calls": len(map_paths) - len(signatures),
        "path_signature_redundancy_percent": round(100 * (len(map_paths) - len(signatures)) / len(map_paths), 4) if map_paths else None,
        "path_signature_frequencies": dict(sorted(signatures.items())),
        "absolute_tuple_union": len(union), "tuple_ids": sorted(union),
        "zero_novelty_calls": sum(value == 0 for value in novelty),
        "zero_novelty_call_percent": round(100 * sum(value == 0 for value in novelty) / len(novelty), 4) if novelty else None,
        "mean_tuples_per_call": round(sum(x["tuples"] for x in per_call) / len(per_call), 4) if per_call else None,
        "mean_marginal_tuples_per_call": round(sum(novelty) / len(novelty), 4) if novelty else None,
        "per_call": per_call,
    }
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("label", "declared_cases", "pytest_returncode", "binary_calls", "distinct_path_signatures", "path_signature_diversity_percent", "duration_seconds")}, indent=2))
    # Bitmap collection remains valid even when qemuafl changes wrapper-visible
    # process semantics (notably ordinary non-zero exits or argv0-sensitive
    # behavior).  Preserve that fact in semantic_replay_status; do not discard
    # successfully captured maps or stop a long cohort.
    return 0 if map_paths else 1


if __name__ == "__main__":
    raise SystemExit(main())
