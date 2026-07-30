#!/usr/bin/env python3
"""Remove candidate cases that fail a V2 quality gate, retaining an audit trail.

Generated pytest bundles name cases with a zero-based ``test_####_`` prefix.
This utility maps only that stable name back to candidates.  It never edits a
V1 manifest or overwrites its input.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


TEST_INDEX = re.compile(r"\.test_(\d{4})_")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--quality-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    report = json.loads(args.quality_report.read_text(encoding="utf-8"))
    rejected: dict[int, str] = {}
    for test_name in report.get("dummy_passing_test_names", []):
        match = TEST_INDEX.search(str(test_name))
        if match:
            rejected[int(match.group(1))] = "dummy_passing"
    original = list(candidates.get("cases") or [])
    retained = [case for index, case in enumerate(original) if index not in rejected]
    repairs = [
        {"candidate_index": index, "case_name": original[index].get("name"), "reason": reason}
        for index, reason in sorted(rejected.items()) if index < len(original)
    ]
    result = dict(candidates)
    result["cases"] = retained
    result["candidate_case_count"] = len(retained)
    result["repair_history"] = list(candidates.get("repair_history") or []) + repairs
    result["quality_gate_source"] = str(args.quality_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"input_cases": len(original), "retained_cases": len(retained), "removed": repairs, "output": str(args.output)}, indent=2))
    return 0 if retained else 1


if __name__ == "__main__":
    raise SystemExit(main())
