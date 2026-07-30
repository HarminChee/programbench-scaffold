#!/usr/bin/env python3
"""Union approved V2 candidate manifests while retaining all repetitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--label", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.manifest) != len(args.label):
        parser.error("--manifest and --label counts must match")
    cases: list[dict] = []
    counts: dict[str, int] = {}
    for path, label in zip(args.manifest, args.label):
        payload = json.loads(path.read_text(encoding="utf-8"))
        incoming = list(payload.get("cases") or [])
        counts[label] = len(incoming)
        for index, raw in enumerate(incoming):
            case = dict(raw)
            case["name"] = f"{label}_{index:04d}_{case.get('name', 'case')}"[:200]
            case["union_source"] = label
            cases.append(case)
    result = {
        "profile": "programbench_oracle_gym_v2_expansive_union",
        "source_policy": "target PB oracle forbidden",
        "duplicate_policy": "retain all approved cases and repetitions",
        "component_counts": counts,
        "candidate_case_count": len(cases),
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"component_counts": counts, "cases": len(cases)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
