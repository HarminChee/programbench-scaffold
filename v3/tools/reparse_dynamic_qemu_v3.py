#!/usr/bin/env python3
"""Recompute QEMU first-party attribution from already collected trace files."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "dynamic_v3", HERE / "run_dynamic_path_metrics_v3.py"
)
assert SPEC and SPEC.loader
DYNAMIC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DYNAMIC)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", type=Path, required=True)
    ap.add_argument("--trace-root", type=Path, required=True)
    ap.add_argument("--executable", type=Path, required=True)
    args = ap.parse_args()

    data = json.loads(args.metrics.read_text())
    module, ranges = DYNAMIC.go_first_party_ranges(args.executable.resolve())
    starts = [start for start, _ in ranges]
    for case in data.get("qemu", {}).get("cases") or []:
        trace = args.trace_root / f"qemu_{int(case['index']):04d}.log"
        pcs: list[int] = []
        for line in trace.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = DYNAMIC.QEMU_PC.search(line)
            if match:
                pcs.append(int(match.group(1), 16))
        blocks = set(pcs)
        edges = {(pcs[index - 1], pcs[index]) for index in range(1, len(pcs))}
        first_blocks = {
            normalized
            for pc in blocks
            if (
                normalized := DYNAMIC.normalize_first_party_pc(pc, starts, ranges)
            ) is not None
        }
        first_edges = {
            (normalized_a, normalized_b)
            for a, b in edges
            if (
                normalized_a := DYNAMIC.normalize_first_party_pc(a, starts, ranges)
            ) is not None
            and (
                normalized_b := DYNAMIC.normalize_first_party_pc(b, starts, ranges)
            ) is not None
        }
        case["first_party_block_ids"] = [
            f"{pc:x}" for pc in sorted(first_blocks)
        ]
        case["first_party_edge_ids"] = [
            f"{a:x}:{b:x}" for a, b in sorted(first_edges)
        ]
        case["first_party_bitmap_ids"] = sorted({
            int(hashlib.blake2s(
                f"{a:x}:{b:x}".encode(), digest_size=2
            ).hexdigest(), 16)
            for a, b in first_edges
        })

    qemu = data["qemu"]
    cases = qemu.get("cases") or []
    qemu["go_module"] = module
    qemu["first_party_text_ranges"] = len(ranges)
    qemu["unique_first_party_blocks_union"] = DYNAMIC.union_metric(
        cases, "first_party_block_ids"
    )
    qemu["unique_first_party_edges_union"] = DYNAMIC.union_metric(
        cases, "first_party_edge_ids"
    )
    qemu["first_party_afl_like_bitmap_slots_union"] = DYNAMIC.union_metric(
        cases, "first_party_bitmap_ids"
    )
    qemu["first_party_edge_novelty"] = DYNAMIC.novelty_curve(
        cases, "first_party_edge_ids"
    )
    DYNAMIC.write(args.metrics, data)
    print(json.dumps({
        "metrics": str(args.metrics),
        "module": module,
        "ranges": len(ranges),
        "first_party_blocks": qemu["unique_first_party_blocks_union"],
        "first_party_edges": qemu["unique_first_party_edges_union"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
