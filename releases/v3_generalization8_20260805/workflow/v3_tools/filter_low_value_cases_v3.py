#!/usr/bin/env python3
"""Filter empty, strict-exact, and excessively repeated V3 oracle cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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
            "terminal", "lifecycle", "sequence", "isolate_home_tmp", "stdin_regular_file", "timeout",
            "timeout_seconds", "observe_files",
        )
    }


def behavior(case: dict) -> dict:
    return {
        key: case.get(key)
        for key in (
            "returncode", "stdout_sha256", "stderr_sha256", "timed_out",
            "observed_files",
            "interactions",
        )
    }


def has_stimulus(case: dict) -> bool:
    if case.get("args"):
        return True
    if case.get("stdin_sha256") not in (None, EMPTY_SHA256):
        return True
    return any(case.get(key) for key in (
        "files", "binary_files", "executable_files", "repeat_files", "env",
        "git", "http", "terminal", "lifecycle", "sequence", "isolate_home_tmp", "stdin_regular_file",
    ))


def has_observation(case: dict) -> bool:
    return (
        int(case.get("stdout_bytes") or 0) > 0
        or int(case.get("stderr_bytes") or 0) > 0
        or bool(case.get("observed_files"))
        or bool(case.get("interactions"))
    )


def adaptive_behavior_limit(group_size: int, floor: int, ceiling: int) -> int:
    """Keep bounded input diversity when many inputs share one observation.

    Identical output does not imply identical path coverage.  A square-root
    allowance retains representative parameter/corpus variation without
    allowing a single behavior group to dominate the suite.
    """
    if floor <= 0:
        return 0
    return max(floor, min(max(floor, ceiling), math.ceil(math.sqrt(max(1, group_size)))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--capture-manifest", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--annotations", type=Path)
    ap.add_argument(
        "--behavior-cap",
        type=int,
        default=5,
        help="Minimum representatives for the adaptive observed-behavior cap; 0 disables the cap.",
    )
    ap.add_argument(
        "--behavior-cap-max",
        type=int,
        default=32,
        help="Upper bound for the adaptive square-root behavior allowance.",
    )
    ap.add_argument(
        "--adaptive-behavior-cap",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Retain additional parameter/corpus representatives for large behavior groups.",
    )
    args = ap.parse_args()

    candidate_data = json.loads(args.candidates.read_text())
    manifest = json.loads(args.capture_manifest.read_text())
    candidates = candidate_data.get("cases") or []
    by_name = {case.get("name"): case for case in candidates}
    annotation_payload = (
        json.loads(args.annotations.read_text(encoding="utf-8-sig"))
        if args.annotations and args.annotations.is_file() else {}
    )
    annotations = annotation_payload.get("cases", annotation_payload) if isinstance(annotation_payload, dict) else {}

    kept_names: list[str] = []
    exact_seen: set[str] = set()
    behavior_counts: Counter[str] = Counter()
    rejected: list[dict] = []
    captured_cases = list(enumerate(manifest.get("cases") or []))
    behavior_group_sizes = Counter(
        digest(behavior(case)) for _, case in captured_cases
    )
    captured_cases.sort(
        key=lambda item: (
            -int((annotations.get(str(item[1].get("name")), {}) or {}).get("priority_score") or 0),
            item[0],
        )
    )
    for original_index, captured in captured_cases:
        name = captured.get("name")
        if name not in by_name:
            rejected.append({"name": name, "reason": "candidate_missing"})
            continue
        annotation = annotations.get(str(name), {}) if isinstance(annotations, dict) else {}
        if str(annotation.get("human_decision") or "").lower() == "reject":
            rejected.append({"name": name, "reason": "human_annotation_reject"})
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
        group_limit = args.behavior_cap
        if args.adaptive_behavior_cap and args.behavior_cap > 0:
            group_limit = adaptive_behavior_limit(
                behavior_group_sizes[behavior_key],
                args.behavior_cap,
                args.behavior_cap_max,
            )
        if group_limit > 0 and behavior_counts[behavior_key] >= group_limit:
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
            "annotation_policy": "human_then_agent_plus_novelty_priority",
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
        "behavior_cap_max": args.behavior_cap_max,
        "adaptive_behavior_cap": args.adaptive_behavior_cap,
        "annotations_used": bool(annotations),
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
