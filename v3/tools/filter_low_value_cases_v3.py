#!/usr/bin/env python3
"""Filter empty, strict-exact, and excessively repeated V3 oracle cases."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def invocation(case: dict) -> dict:
    return {
        key: case.get(key)
        for key in (
            "args", "env", "stdin_sha256", "files", "binary_files",
            "executable_files", "repeat_files", "file_modes", "git", "http",
            "terminal", "isolate_home_tmp", "stdin_regular_file", "timeout",
            "timeout_seconds", "observe_files",
        )
    }


def behavior(case: dict) -> dict:
    return {
        key: case.get(key)
        for key in (
            "returncode", "stdout_sha256", "stderr_sha256", "timed_out",
            "observed_files",
        )
    }


def has_stimulus(case: dict) -> bool:
    if case.get("args"):
        return True
    if case.get("stdin_sha256") not in (None, EMPTY_SHA256):
        return True
    return any(case.get(key) for key in (
        "files", "binary_files", "executable_files", "repeat_files", "env",
        "git", "http", "terminal", "isolate_home_tmp", "stdin_regular_file",
    ))


def has_observation(case: dict) -> bool:
    return (
        int(case.get("stdout_bytes") or 0) > 0
        or int(case.get("stderr_bytes") or 0) > 0
        or bool(case.get("observed_files"))
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--capture-manifest", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument(
        "--behavior-cap",
        type=int,
        default=0,
        help="Maximum cases per observed behavior; 0 disables behavior-only pruning.",
    )
    args = ap.parse_args()

    candidate_data = json.loads(args.candidates.read_text())
    manifest = json.loads(args.capture_manifest.read_text())
    candidates = candidate_data.get("cases") or []
    by_name = {case.get("name"): case for case in candidates}

    kept_names: list[str] = []
    exact_seen: set[str] = set()
    behavior_counts: Counter[str] = Counter()
    rejected: list[dict] = []
    for captured in manifest.get("cases") or []:
        name = captured.get("name")
        if name not in by_name:
            rejected.append({"name": name, "reason": "candidate_missing"})
            continue
        if not has_stimulus(captured) and not has_observation(captured):
            rejected.append({"name": name, "reason": "empty_no_stimulus_or_observation"})
            continue
        if not has_observation(captured):
            rejected.append({"name": name, "reason": "returncode_only_weak_oracle"})
            continue
        invocation_key = digest(invocation(captured))
        behavior_key = digest(behavior(captured))
        exact_key = digest((invocation_key, behavior_key))
        if exact_key in exact_seen:
            rejected.append({"name": name, "reason": "strict_exact_execution_duplicate"})
            continue
        if args.behavior_cap > 0 and behavior_counts[behavior_key] >= args.behavior_cap:
            rejected.append({"name": name, "reason": "identical_behavior_group_over_cap"})
            continue
        exact_seen.add(exact_key)
        behavior_counts[behavior_key] += 1
        kept_names.append(name)

    kept_set = set(kept_names)
    output_data = {
        **candidate_data,
        "profile": "programbench_oracle_gym_v3_quality_filtered",
        "cases": [case for case in candidates if case.get("name") in kept_set],
        "candidate_case_count": len(kept_names),
        "quality_filter": {
            "behavior_cap": args.behavior_cap,
            "retained": len(kept_names),
            "rejected": len(rejected),
        },
    }
    reasons = Counter(item["reason"] for item in rejected)
    report = {
        "schema": "programbench_oracle_gym_v3_low_value_filter",
        "input_candidates": len(candidates),
        "captured_cases": len(manifest.get("cases") or []),
        "retained_cases": len(kept_names),
        "rejected_cases": len(rejected),
        "rejection_reasons": dict(sorted(reasons.items())),
        "behavior_cap": args.behavior_cap,
        "rejected": rejected,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output_data, indent=2, ensure_ascii=False) + "\n")
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({key: report[key] for key in (
        "input_candidates", "captured_cases", "retained_cases",
        "rejected_cases", "rejection_reasons",
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
