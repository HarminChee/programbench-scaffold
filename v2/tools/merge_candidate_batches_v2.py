#!/usr/bin/env python3
"""Merge V2 topic-batch candidates and remove exact invocation duplicates."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


NON_EXECUTION_FIELDS = {"name", "area", "origin", "rationale", "source_batch"}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    inputs = sorted(args.candidate_dir.glob("batch_*.json"))
    retained: list[dict] = []
    seen: dict[str, dict] = {}
    removed: list[dict] = []
    batch_counts: dict[str, int] = {}
    for path in inputs:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = list(payload.get("cases") or [])
        batch_counts[path.stem] = len(cases)
        for case in cases:
            execution = {key: value for key, value in case.items() if key not in NON_EXECUTION_FIELDS}
            signature = hashlib.sha256(canonical(execution).encode()).hexdigest()
            if signature in seen:
                removed.append({
                    "reason": "exact_invocation_duplicate",
                    "case_name": case.get("name"),
                    "kept_case_name": seen[signature].get("name"),
                    "source_batch": path.stem,
                    "signature": signature,
                })
                continue
            unique = dict(case)
            unique["source_batch"] = path.stem
            seen[signature] = unique
            retained.append(unique)

    result = {
        "profile": "programbench_oracle_gym_v2",
        "source_policy": "target PB oracle forbidden",
        "input_batch_count": len(inputs),
        "input_case_count": sum(batch_counts.values()),
        "candidate_case_count": len(retained),
        "exact_invocation_duplicates_removed": len(removed),
        "batch_counts": batch_counts,
        "removed_cases": removed,
        "cases": retained,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "input_batch_count", "input_case_count", "candidate_case_count",
        "exact_invocation_duplicates_removed"
    )}, indent=2))
    return 0 if retained else 1


if __name__ == "__main__":
    raise SystemExit(main())
