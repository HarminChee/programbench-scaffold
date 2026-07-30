#!/usr/bin/env python3
"""Build a repaired candidate manifest from captured and quality-approved cases."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


TEST_NAME = re.compile(r"\.test_\d{4}_(.+)$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--capture-manifest", type=Path, required=True)
    parser.add_argument("--quality-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    capture = json.loads(args.capture_manifest.read_text(encoding="utf-8"))
    quality = json.loads(args.quality_report.read_text(encoding="utf-8"))
    captured_names = [str(case["name"]) for case in capture.get("cases") or []]
    dummy_names = set()
    for full_name in quality.get("dummy_passing_test_names") or []:
        match = TEST_NAME.search(str(full_name))
        if match:
            dummy_names.add(match.group(1))
    repeat_names = set()
    repeat_summary = (quality.get("repeat_check") or {}).get("junit_summary") or {}
    for full_name in [
        *(repeat_summary.get("failed_test_names") or []),
        *(repeat_summary.get("error_test_names") or []),
    ]:
        match = TEST_NAME.search(str(full_name))
        if match:
            repeat_names.add(match.group(1))
    captured_by_index = {
        index: str(case["name"]) for index, case in enumerate(capture.get("cases") or [])
    }
    source_leak_names = set()
    for item in (quality.get("source_leak_scan") or {}).get("source_identifying_matches") or []:
        leak_path = str(item.get("path") or "").replace("\\", "/")
        filename = Path(leak_path).name
        # Fixture directory indices originate from the pre-capture candidate
        # list, while capture["cases"] is compacted after skipped cases.  The
        # embedded case name is therefore the authoritative mapping.
        named_match = next(
            (
                name
                for name in sorted(captured_names, key=len, reverse=True)
                if f"_{name}." in leak_path or f"_{name}/" in leak_path
            ),
            None,
        )
        if named_match:
            source_leak_names.add(named_match)
            continue
        index_match = re.match(r"(\d{3,})_", filename)
        if index_match:
            captured_name = captured_by_index.get(int(index_match.group(1)))
            if captured_name:
                source_leak_names.add(captured_name)
    rejected_names = dummy_names | repeat_names | source_leak_names
    approved = set(captured_names) - rejected_names
    retained = [case for case in candidates.get("cases") or [] if str(case.get("name")) in approved]
    result = dict(candidates)
    result["cases"] = retained
    result["candidate_case_count"] = len(retained)
    result["capture_skipped_count"] = len(candidates.get("cases") or []) - len(captured_names)
    result["quality_rejected_names"] = sorted(rejected_names)
    result["repeat_rejected_names"] = sorted(repeat_names)
    result["source_leak_rejected_names"] = sorted(source_leak_names)
    result["repair_history"] = list(candidates.get("repair_history") or []) + [
        *[{"case_name": name, "reason": "dummy_passing"} for name in sorted(dummy_names)],
        *[
            {"case_name": name, "reason": "repeat_failure"}
            for name in sorted(repeat_names - dummy_names)
        ],
        *[
            {"case_name": name, "reason": "source_leak"}
            for name in sorted(source_leak_names - dummy_names - repeat_names)
        ],
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "source_candidates": len(candidates.get("cases") or []),
        "captured": len(captured_names),
        "dummy_rejected": len(dummy_names),
        "repeat_rejected": len(repeat_names),
        "source_leak_rejected": len(source_leak_names),
        "retained": len(retained),
    }, indent=2))
    return 0 if retained else 1


if __name__ == "__main__":
    raise SystemExit(main())
