#!/usr/bin/env python3
"""Combine all expansive V2 candidates without removing duplicates."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


META = {"name", "area", "origin", "rationale"}


def signature(case: dict) -> str:
    execution = {key: value for key, value in case.items() if key not in META}
    body = json.dumps(execution, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases: list[dict] = []
    signatures: Counter[str] = Counter()
    batch_counts: dict[str, int] = {}
    for path in sorted(args.candidate_dir.glob("batch_*.json")):
        # PowerShell 5 writes UTF-8 JSON with a BOM for explicit rejected-batch
        # audit manifests. Accept both that form and regular UTF-8 manifests.
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        batch = path.stem
        incoming = list(payload.get("cases") or [])
        batch_counts[batch] = len(incoming)
        for index, raw in enumerate(incoming):
            case = dict(raw)
            original_name = str(case.get("name") or f"case_{index:04d}")
            case["name"] = f"{batch}_{index:04d}_{original_name}"[:180]
            case["source_batch"] = batch
            signatures[signature(case)] += 1
            cases.append(case)
    duplicate_count = sum(count - 1 for count in signatures.values() if count > 1)
    result = {
        "profile": "programbench_oracle_gym_v2_expansive",
        "source_policy": "target PB oracle forbidden",
        "duplicate_policy": "retain exact, invocation, structural, and behavior repetition",
        "input_batch_count": len(batch_counts),
        "candidate_case_count": len(cases),
        "exact_invocation_repetitions_retained": duplicate_count,
        "batch_counts": batch_counts,
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "batches": len(batch_counts),
        "cases": len(cases),
        "exact_invocation_repetitions_retained": duplicate_count,
    }, indent=2))
    return 0 if cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
