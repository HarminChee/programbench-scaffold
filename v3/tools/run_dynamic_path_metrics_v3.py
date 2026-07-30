#!/usr/bin/env python3
"""Collect optional Callgrind, QEMU-TB, and AFL-like secondary path signals.

This runner intentionally reports dynamic observations, not a percentage of all
possible paths. It supports the common flat fixture subset directly and records
unsupported cases instead of silently changing their semantics.
"""

from __future__ import annotations

import argparse
import base64
import bisect
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


# QEMU's first hexadecimal field is the host translation-cache address and
# changes between runs. The second field inside brackets is the guest TB PC.
QEMU_PC = re.compile(r"Trace\s+\d+:\s+0x[0-9a-fA-F]+\s+\[[0-9a-fA-F]+/([0-9a-fA-F]+)/")
CALLGRIND_FN = re.compile(r"^(?:fn|cfn)=\(?\d*\)?\s*(.*)$")
CALLGRIND_POS = re.compile(r"^0x[0-9a-fA-F]+")


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def available(name: str) -> str | None:
    return shutil.which(name)


def go_first_party_ranges(executable: Path) -> tuple[str | None, list[tuple[int, int]]]:
    go = available("go")
    if not go and Path("/usr/local/go1.21.13/bin/go").is_file():
        go = "/usr/local/go1.21.13/bin/go"
    if not go:
        return None, []
    version = subprocess.run(
        [go, "version", "-m", str(executable)], capture_output=True, text=True
    )
    module = None
    for line in version.stdout.splitlines():
        fields = line.strip().split()
        if len(fields) >= 2 and fields[0] == "path":
            module = fields[1]
            break
    if not module:
        return None, []
    symbols = subprocess.run(
        [go, "tool", "nm", str(executable)], capture_output=True, text=True
    )
    parsed: list[tuple[int, str, str]] = []
    for line in symbols.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3:
            continue
        try:
            address = int(fields[0], 16)
        except ValueError:
            continue
        parsed.append((address, fields[1], fields[2]))
    parsed.sort()
    unique_addresses = sorted({address for address, _, _ in parsed})
    ranges: list[tuple[int, int]] = []
    for start, symbol_type, name in parsed:
        if symbol_type not in {"T", "t"}:
            continue
        if not (name.startswith(module + "/") or name.startswith(module + ".") or name.startswith("main.")):
            continue
        next_index = bisect.bisect_right(unique_addresses, start)
        end = unique_addresses[next_index] if next_index < len(unique_addresses) else start + 1
        if end > start:
            ranges.append((start, end))
    return module, ranges


def in_ranges(pc: int, starts: list[int], ranges: list[tuple[int, int]]) -> bool:
    index = bisect.bisect_right(starts, pc) - 1
    return index >= 0 and pc < ranges[index][1]


def normalize_first_party_pc(
    pc: int, starts: list[int], ranges: list[tuple[int, int]]
) -> int | None:
    # qemu-user maps dynamically linked x86-64 executables above a guest base
    # (commonly 0x4000000000), while `go tool nm` reports ELF-relative VAs.
    for bias in (0, 0x4000000000):
        candidate = pc - bias
        if candidate >= 0 and in_ranges(candidate, starts, ranges):
            return candidate
    return None


def materialize(case: dict, root: Path) -> tuple[bool, str]:
    unsupported = [
        name for name in ("http", "terminal")
        if case.get(name)
    ]
    git = case.get("git")
    if git:
        unsupported.append("git")
    if unsupported:
        return False, "unsupported fixture providers: " + ",".join(unsupported)
    for field, binary in (("files", False), ("executable_files", False), ("binary_files", True)):
        for rel, content in (case.get(field) or {}).items():
            relative = Path(str(rel))
            if relative.is_absolute() or ".." in relative.parts:
                return False, f"unsafe path: {rel}"
            dest = root / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            if binary:
                dest.write_bytes(base64.b64decode(str(content)))
            else:
                if isinstance(content, dict):
                    content = content.get("content", "")
                dest.write_text(str(content), encoding="utf-8")
            if field == "executable_files":
                dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
    for rel, mode in (case.get("file_modes") or {}).items():
        path = root / str(rel)
        if path.exists():
            path.chmod(int(str(mode), 8) if isinstance(mode, str) else int(mode))
    return True, ""


def run(cmd: list[str], *, cwd: Path, env: dict[str, str], stdin: bytes, timeout: float) -> dict:
    started = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, input=stdin, capture_output=True, timeout=timeout)
        return {
            "returncode": proc.returncode,
            "timed_out": False,
            "command_runtime_seconds": round(time.perf_counter() - started, 6),
            "stdout_sha256": hashlib.sha256(proc.stdout).hexdigest(),
            "stderr_tail": proc.stderr.decode("utf-8", errors="replace")[-2000:],
        }
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or b""
        if isinstance(stderr, str):
            stderr = stderr.encode()
        return {
            "returncode": None,
            "timed_out": True,
            "command_runtime_seconds": round(time.perf_counter() - started, 6),
            "stdout_sha256": None,
            "stderr_tail": stderr.decode("utf-8", errors="replace")[-2000:],
        }


def case_stdin(case: dict, manifest_root: Path) -> bytes:
    inline = case.get("stdin")
    if inline is not None:
        return str(inline).encode()
    stdin_file = case.get("stdin_file")
    if stdin_file:
        source = manifest_root / str(stdin_file)
        if not source.is_file():
            matches = list((manifest_root / "fixtures").glob(f"**/{Path(str(stdin_file)).name}"))
            source = matches[0] if len(matches) == 1 else source
        if source.is_file():
            return source.read_bytes()
    return b""


def case_timeout(case: dict, fallback: float) -> float:
    return float(case.get("timeout_seconds") or case.get("timeout") or fallback)


def qemu_case(
    qemu: str, executable: Path, case: dict, root: Path, trace: Path, manifest_root: Path,
    first_party_ranges: list[tuple[int, int]],
) -> dict:
    collector_started = time.perf_counter()
    cmd = [
        qemu, "-0", str(case.get("argv0") or "/workspace/executable"),
        "-d", "exec,nochain", "-D", str(trace), str(executable), *(case.get("args") or []),
    ]
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in (case.get("env") or {}).items()})
    observed = run(
        cmd, cwd=root, env=env, stdin=case_stdin(case, manifest_root),
        timeout=case_timeout(case, 12),
    )
    pcs = []
    if trace.is_file():
        for line in trace.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = QEMU_PC.search(line)
            if match:
                pcs.append(int(match.group(1), 16))
    blocks = set(pcs)
    edges = {(pcs[i - 1], pcs[i]) for i in range(1, len(pcs))}
    range_starts = [item[0] for item in first_party_ranges]
    first_party_blocks = {
        normalized
        for pc in blocks
        if (normalized := normalize_first_party_pc(
            pc, range_starts, first_party_ranges
        )) is not None
    }
    first_party_edges = {
        (normalized_a, normalized_b)
        for a, b in edges
        if (normalized_a := normalize_first_party_pc(
            a, range_starts, first_party_ranges
        )) is not None
        and (normalized_b := normalize_first_party_pc(
            b, range_starts, first_party_ranges
        )) is not None
    }
    bitmap = {int(hashlib.blake2s(f"{a:x}:{b:x}".encode(), digest_size=2).hexdigest(), 16) for a, b in edges}
    first_party_bitmap = {
        int(hashlib.blake2s(f"{a:x}:{b:x}".encode(), digest_size=2).hexdigest(), 16)
        for a, b in first_party_edges
    }
    return {
        **observed,
        "collector_runtime_seconds": round(time.perf_counter() - collector_started, 6),
        "executed_tb_events": len(pcs),
        "unique_translation_blocks": len(blocks),
        "unique_block_edges": len(edges),
        "afl_like_bitmap_slots": len(bitmap),
        "block_ids": [f"{pc:x}" for pc in sorted(blocks)],
        "edge_ids": [f"{a:x}:{b:x}" for a, b in sorted(edges)],
        "first_party_block_ids": [f"{pc:x}" for pc in sorted(first_party_blocks)],
        "first_party_edge_ids": [f"{a:x}:{b:x}" for a, b in sorted(first_party_edges)],
        "bitmap_ids": sorted(bitmap),
        "first_party_bitmap_ids": sorted(first_party_bitmap),
    }


def callgrind_case(
    valgrind: str, executable: Path, case: dict, root: Path, profile: Path, manifest_root: Path
) -> dict:
    collector_started = time.perf_counter()
    cmd = [
        valgrind, "--tool=callgrind", "--dump-instr=yes", "--collect-jumps=yes",
        f"--callgrind-out-file={profile}", str(executable), *(case.get("args") or []),
    ]
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in (case.get("env") or {}).items()})
    observed = run(
        cmd, cwd=root, env=env, stdin=case_stdin(case, manifest_root),
        timeout=case_timeout(case, 20),
    )
    functions, positions, jumps = set(), set(), 0
    if profile.is_file():
        for line in profile.read_text(encoding="utf-8", errors="ignore").splitlines():
            fn = CALLGRIND_FN.match(line)
            if fn:
                functions.add(fn.group(1))
            if CALLGRIND_POS.match(line):
                positions.add(line.split()[0])
            if line.startswith("jump=") or line.startswith("jcnd="):
                jumps += 1
    return {
        **observed,
        "collector_runtime_seconds": round(time.perf_counter() - collector_started, 6),
        "unique_functions": len(functions),
        "unique_instruction_positions": len(positions),
        "jump_records": jumps,
        "function_ids": sorted(functions),
        "instruction_ids": sorted(positions),
    }


def union_metric(results: list[dict], key: str) -> int:
    values = set()
    for result in results:
        values.update(result.get(key, []))
    return len(values)


def novelty_curve(results: list[dict], key: str) -> dict:
    union: set[Any] = set()
    curve = []
    for result in results:
        current = set(result.get(key, []))
        added = current - union
        union.update(current)
        curve.append({
            "index": result.get("index"),
            "name": result.get("name"),
            "marginal_new": len(added),
            "union_after": len(union),
        })
    return {
        "cases_with_new_signal": sum(item["marginal_new"] > 0 for item in curve),
        "cases_with_no_new_signal": sum(item["marginal_new"] == 0 for item in curve),
        "curve": curve,
    }


def main() -> int:
    collector_started = time.perf_counter()
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--executable", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--work-root", type=Path, required=True)
    ap.add_argument("--max-cases", type=int, default=80)
    ap.add_argument("--sampling", choices=("uniform", "head"), default="uniform")
    ap.add_argument("--skip-qemu", action="store_true")
    ap.add_argument("--skip-callgrind", action="store_true")
    ap.add_argument(
        "--discard-raw-traces",
        action="store_true",
        help="Delete parsed QEMU/Callgrind raw files to bound disk use.",
    )
    args = ap.parse_args()
    manifest = json.loads(args.manifest.read_text())
    manifest_root = args.manifest.resolve().parent
    all_cases = list(manifest.get("cases") or [])
    requested = min(args.max_cases, len(all_cases))
    if args.sampling == "head" or requested >= len(all_cases):
        selected_indexes = list(range(requested))
    elif requested <= 1:
        selected_indexes = [0] if requested else []
    else:
        selected_indexes = sorted({
            round(i * (len(all_cases) - 1) / (requested - 1))
            for i in range(requested)
        })
    cases = [(index, all_cases[index]) for index in selected_indexes]
    executable = args.executable.resolve()
    module, first_party_ranges = go_first_party_ranges(executable)
    tools = {
        "valgrind": available("valgrind"),
        "qemu_x86_64": available("qemu-x86_64"),
        "afl_showmap": available("afl-showmap"),
        "afl_qemu_trace": available("afl-qemu-trace"),
    }
    qemu_results, callgrind_results, skipped = [], [], []
    args.work_root.mkdir(parents=True, exist_ok=True)
    for index, case in cases:
        with tempfile.TemporaryDirectory(prefix=f"v3_dynamic_{index:04d}_", dir=args.work_root) as temp:
            root = Path(temp)
            ok, reason = materialize(case, root)
            if not ok:
                skipped.append({"index": index, "name": case.get("name"), "reason": reason})
                continue
            if tools["qemu_x86_64"] and not args.skip_qemu:
                result = qemu_case(
                    tools["qemu_x86_64"], executable, case, root,
                    args.work_root / f"qemu_{index:04d}.log", manifest_root, first_party_ranges,
                )
                result["returncode_matches_oracle"] = result.get("returncode") == case.get("returncode")
                result["stdout_matches_oracle"] = result.get("stdout_sha256") == case.get("stdout_sha256")
                qemu_results.append({"index": index, "name": case.get("name"), **result})
                if args.discard_raw_traces:
                    (args.work_root / f"qemu_{index:04d}.log").unlink(missing_ok=True)
            if tools["valgrind"] and not args.skip_callgrind:
                result = callgrind_case(
                    tools["valgrind"], executable, case, root,
                    args.work_root / f"callgrind_{index:04d}.out", manifest_root,
                )
                result["returncode_matches_oracle"] = result.get("returncode") == case.get("returncode")
                result["stdout_matches_oracle"] = result.get("stdout_sha256") == case.get("stdout_sha256")
                callgrind_results.append({"index": index, "name": case.get("name"), **result})
                if args.discard_raw_traces:
                    (args.work_root / f"callgrind_{index:04d}.out").unlink(missing_ok=True)
    result = {
        "schema": "programbench_oracle_gym_v3_dynamic_path_metrics",
        "interpretation": "secondary dynamic observations; not total-path coverage percentages",
        "tools": {
            "valgrind": {"available": bool(tools["valgrind"]), "path": tools["valgrind"]},
            "qemu_x86_64": {"available": bool(tools["qemu_x86_64"]), "path": tools["qemu_x86_64"]},
            "afl_showmap": {
                "available": bool(tools["afl_showmap"]),
                "path": tools["afl_showmap"],
                "formal_metric_collected": False,
                "qemu_backend_available": bool(tools["afl_qemu_trace"]),
                "reason": (
                    "plain Go binaries are not AFL-instrumented and the installed package has no "
                    "afl-qemu-trace backend; QEMU-derived bitmap is reported separately"
                    if not tools["afl_qemu_trace"]
                    else "plain Go binaries are not AFL-instrumented; a separate AFL QEMU-mode adapter is required"
                ),
            },
        },
        "requested_cases": len(cases),
        "sampling": args.sampling,
        "selected_indexes": selected_indexes,
        "unsupported_cases": skipped,
        "qemu": {
            "completed_cases": len(qemu_results),
            "go_module": module,
            "first_party_text_ranges": len(first_party_ranges),
            "unique_translation_blocks_union": union_metric(qemu_results, "block_ids"),
            "unique_block_edges_union": union_metric(qemu_results, "edge_ids"),
            "unique_first_party_blocks_union": union_metric(qemu_results, "first_party_block_ids"),
            "unique_first_party_edges_union": union_metric(qemu_results, "first_party_edge_ids"),
            "afl_like_bitmap_slots_union": union_metric(qemu_results, "bitmap_ids"),
            "first_party_afl_like_bitmap_slots_union": union_metric(
                qemu_results, "first_party_bitmap_ids"
            ),
            "first_party_edge_novelty": novelty_curve(qemu_results, "first_party_edge_ids"),
            "cases": qemu_results,
        },
        "callgrind": {
            "completed_cases": len(callgrind_results),
            "unique_functions_union": union_metric(callgrind_results, "function_ids"),
            "unique_instruction_positions_union": union_metric(callgrind_results, "instruction_ids"),
            "cases": callgrind_results,
        },
    }
    qemu_seconds = sum(float(item.get("collector_runtime_seconds") or 0) for item in qemu_results)
    callgrind_seconds = sum(float(item.get("collector_runtime_seconds") or 0) for item in callgrind_results)
    result["timing"] = {
        "unit": "seconds",
        "total_collector_runtime_seconds": round(time.perf_counter() - collector_started, 6),
        "qemu_runtime_seconds": round(qemu_seconds, 6),
        "qemu_mean_case_seconds": round(qemu_seconds / len(qemu_results), 6) if qemu_results else None,
        "qemu_completed_cases": len(qemu_results),
        "callgrind_runtime_seconds": round(callgrind_seconds, 6),
        "callgrind_mean_case_seconds": (
            round(callgrind_seconds / len(callgrind_results), 6)
            if callgrind_results
            else None
        ),
        "callgrind_completed_cases": len(callgrind_results),
        "definition": (
            "Per-backend wall-clock time includes target execution and trace/profile parsing; "
            "total collector time also includes fixture materialization and tool discovery."
        ),
    }
    # Whole-process QEMU ID arrays can reach gigabytes for large cgo binaries.
    # Keep aggregate counts and first-party IDs/novelty, but omit raw runtime
    # and library IDs from the durable JSON artifact.
    for case in result["qemu"]["cases"]:
        case.pop("block_ids", None)
        case.pop("edge_ids", None)
        case.pop("bitmap_ids", None)
    write(args.output, result)
    print(json.dumps({
        "tools": result["tools"],
        "requested_cases": len(cases),
        "unsupported_cases": len(skipped),
        "qemu_cases": len(qemu_results),
        "callgrind_cases": len(callgrind_results),
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
