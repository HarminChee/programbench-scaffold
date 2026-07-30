#!/usr/bin/env python3
"""Combine every valid V3 batch case and retain all repetitions."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def sig(case: dict) -> str:
    execution = {k: v for k, v in case.items() if k not in {"name", "area", "origin", "rationale", "source_batch"}}
    return hashlib.sha256(json.dumps(execution, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    cases, counts, batches = [], Counter(), {}
    for path in sorted(args.candidate_dir.glob("batch_*.json")):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        incoming = data.get("cases", [])
        batches[path.stem] = len(incoming)
        for index, raw in enumerate(incoming):
            case = dict(raw)
            case["source_batch"] = path.stem
            case["name"] = f"{path.stem}_{index:04d}_{case.get('name', 'case')}"[:190]
            counts[sig(case)] += 1
            cases.append(case)
    repetitions = sum(value - 1 for value in counts.values() if value > 1)
    result = {
        "profile": "programbench_oracle_gym_v3",
        "source_policy": "target PB oracle forbidden",
        "duplicate_policy": "retain every valid case",
        "candidate_case_count": len(cases),
        "exact_execution_repetitions_retained": repetitions,
        "batch_counts": batches,
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"batches": len(batches), "cases": len(cases), "repetitions": repetitions}, indent=2))
    return 0 if cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
