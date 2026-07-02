#!/usr/bin/env python3
"""Summarize ProgramBench-style black-box behavior probe results."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def short(value: str, limit: int) -> str:
    value = value.replace("\n", "\\n")
    return value[:limit] + ("..." if len(value) > limit else "")


def summarize(payload: dict[str, Any], examples: int, excerpt_limit: int) -> dict[str, Any]:
    results = payload.get("results") or []
    check_totals: Counter[str] = Counter()
    check_passes: Counter[str] = Counter()
    mismatch_examples = []

    for item in results:
        comparison = item.get("comparison") or {}
        checks = comparison.get("checks") or {}
        for name, passed in checks.items():
            check_totals[name] += 1
            if passed:
                check_passes[name] += 1

        if comparison.get("exact_match"):
            continue
        if len(mismatch_examples) >= examples:
            continue
        reference = item.get("reference") or {}
        candidate = item.get("candidate") or {}
        mismatch_examples.append(
            {
                "case": (item.get("case") or {}).get("name"),
                "args": (item.get("case") or {}).get("args", []),
                "partial_reward": comparison.get("partial_reward"),
                "failed_checks": [name for name, passed in checks.items() if not passed],
                "reference": {
                    "returncode": reference.get("returncode"),
                    "stdout": short(reference.get("stdout") or "", excerpt_limit),
                    "stderr": short(reference.get("stderr") or "", excerpt_limit),
                },
                "candidate": {
                    "returncode": candidate.get("returncode"),
                    "stdout": short(candidate.get("stdout") or "", excerpt_limit),
                    "stderr": short(candidate.get("stderr") or "", excerpt_limit),
                },
            }
        )

    check_rates = {
        name: {
            "passed": check_passes[name],
            "total": check_totals[name],
            "rate": round(check_passes[name] / check_totals[name], 4) if check_totals[name] else None,
        }
        for name in sorted(check_totals)
    }
    return {
        "summary": payload.get("summary") or {},
        "check_rates": check_rates,
        "mismatch_examples": mismatch_examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_json", type=Path)
    parser.add_argument("--examples", type=int, default=5)
    parser.add_argument("--excerpt-limit", type=int, default=180)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.result_json.read_text(encoding="utf-8"))
    report = summarize(payload, args.examples, args.excerpt_limit)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
