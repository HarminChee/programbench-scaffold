#!/usr/bin/env python3
"""Remove successful network-dependent cases that lack a loopback fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--capture-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    log = json.loads(args.capture_log.read_text(encoding="utf-8"))
    capture = json.loads(log["stdout"])
    forbidden: set[str] = set()
    for case in capture.get("cases") or []:
        args_list = case.get("args") or []
        is_update = any(arg in {"-u", "--update"} for arg in args_list)
        if is_update and case.get("returncode") == 0 and not case.get("http"):
            forbidden.add(str(case["name"]))

    source_cases = candidates.get("cases") or []
    retained = [case for case in source_cases if str(case.get("name")) not in forbidden]
    result = dict(candidates)
    result["cases"] = retained
    result["candidate_case_count"] = len(retained)
    result["external_network_rejected_names"] = sorted(forbidden)
    result["repair_history"] = list(candidates.get("repair_history") or []) + [
        {"case_name": name, "reason": "successful_update_without_loopback_fixture"}
        for name in sorted(forbidden)
    ]
    result["generation_policy"] = {
        **(candidates.get("generation_policy") or {}),
        "external_network_forbidden": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "source_candidates": len(source_cases),
                "external_network_rejected": len(forbidden),
                "retained": len(retained),
            },
            indent=2,
        )
    )
    return 0 if retained else 1


if __name__ == "__main__":
    raise SystemExit(main())
