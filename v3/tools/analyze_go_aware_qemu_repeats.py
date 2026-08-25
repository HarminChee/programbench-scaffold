#!/usr/bin/env python3
"""Analyze repeat stability for Go-aware AFL++ QEMU edge maps."""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path


def load_maps(root: Path, oracle_passing_only: bool = False, max_calls: int | None = None) -> list[frozenset[int]]:
    paths = sorted(root.glob("*.map"))
    if oracle_passing_only:
        result = json.loads((root.parent / "result.json").read_text())
        selected = result.get("call_sampling", {}).get("selected_pytest_nodes") or []
        failed = set(result.get("oracle_passing_metrics", {}).get("failed_nodes") or [])
        if len(selected) != len(paths):
            raise SystemExit(f"node/call alignment unavailable for {root}: {len(selected)} nodes, {len(paths)} maps")
        paths = [path for path, node in zip(paths, selected) if node not in failed]
    if max_calls is not None:
        paths = paths[:max_calls]
    return [
        frozenset(int(line.split(":", 1)[0]) for line in path.read_text().splitlines() if ":" in line)
        for path in paths
    ]


def jaccard(left: frozenset[int], right: frozenset[int]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--maps", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--oracle-passing-only", action="store_true")
    parser.add_argument("--max-calls", type=int)
    args = parser.parse_args()
    repeats = [load_maps(path, args.oracle_passing_only, args.max_calls) for path in args.maps]
    counts = [len(run) for run in repeats]
    if len(set(counts)) != 1:
        raise SystemExit(f"repeat call-count mismatch: {counts}")
    if not counts or counts[0] == 0:
        raise SystemExit("no aligned calls remain for repeat-stability analysis")

    stable = [frozenset.intersection(*(run[index] for run in repeats)) for index in range(counts[0])]
    exact_repeat_calls = sum(all(run[index] == repeats[0][index] for run in repeats[1:]) for index in range(counts[0]))
    per_call_pairwise = [
        jaccard(repeats[a][index], repeats[b][index])
        for index in range(counts[0])
        for a, b in combinations(range(len(repeats)), 2)
    ]
    raw_unions = [frozenset().union(*run) for run in repeats]
    stable_union = frozenset().union(*stable)
    mean_jaccard = sum(per_call_pairwise) / len(per_call_pairwise) if per_call_pairwise else 1.0
    min_jaccard = min(per_call_pairwise) if per_call_pairwise else 1.0
    suite_jaccards = [
        jaccard(raw_unions[a], raw_unions[b])
        for a, b in combinations(range(len(raw_unions)), 2)
    ]
    retention = [100 * len(stable_union) / len(item) if item else 100.0 for item in raw_unions]
    stability_accepted = len(repeats) >= 3 and mean_jaccard >= 0.98 and min(retention) >= 95.0
    document = {
        "schema": "programbench_go_aware_afl_qemu_repeat_stability_v1",
        "scope": "oracle-passing calls only" if args.oracle_passing_only else "all captured calls",
        "repeat_count": len(repeats),
        "calls": counts[0],
        "requested_max_calls": args.max_calls,
        "raw_edge_unions": [len(item) for item in raw_unions],
        "stable_edge_union": len(stable_union),
        "stable_path_coverage": len(set(stable)),
        "exact_repeat_calls": exact_repeat_calls,
        "mean_per_call_pairwise_jaccard": round(mean_jaccard, 6),
        "min_per_call_pairwise_jaccard": round(min_jaccard, 6),
        "suite_union_pairwise_jaccard": [round(value, 6) for value in suite_jaccards],
        "stable_edge_retention_percent": [round(value, 2) for value in retention],
        "stability_gate": {
            "accepted": stability_accepted,
            "criteria": {
                "repeat_count_at_least": 3,
                "mean_per_call_pairwise_jaccard_at_least": 0.98,
                "stable_edge_retention_each_run_percent_at_least": 95.0
            }
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n")
    print(json.dumps(document, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
