from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any


STATIC_FIELDS = (
    "argv0",
    "args",
    "stdin",
    "env",
    "files",
    "binary_files",
    "executable_files",
    "repeat_files",
    "file_modes",
    "git",
    "http",
    "terminal",
    "lifecycle",
    "sequence",
    "observe_files",
    "isolate_home_tmp",
    "stdin_regular_file",
    "timeout_seconds",
)


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def exact_key(case: dict[str, Any]) -> str:
    return _digest({field: case.get(field) for field in STATIC_FIELDS})


def family_key(case: dict[str, Any]) -> str:
    arguments = case.get("args") or []
    command = str(arguments[0]) if arguments else "<root>"
    fixture_shape = tuple(field for field in STATIC_FIELDS[3:] if case.get(field))
    return _stable(
        (
            str(case.get("area") or "unknown"),
            command,
            fixture_shape,
            str(case.get("intended_oracle_class") or "normal"),
        )
    )


def value_signals(case: dict[str, Any]) -> set[str]:
    evidence = case.get("value_evidence") or {}
    signals = {
        name
        for name in (
            "new_coverage",
            "new_behavior",
            "new_error_class",
            "new_fixture_shape",
            "new_assertion_class",
            "stronger_oracle",
        )
        if bool(evidence.get(name))
    }
    if case.get("lifecycle") or case.get("sequence") or case.get("terminal"):
        signals.add("stateful_stimulus")
    return signals


@dataclass(frozen=True)
class TrancheSelection:
    selected: list[dict[str, Any]]
    deferred: list[dict[str, Any]]
    duplicates: list[str]
    fuse_tripped: bool
    report: dict[str, Any]


def select_tranche(
    cases: list[dict[str, Any]],
    *,
    preferred_size: int,
    safety_fuse: int,
    family_quota: int,
) -> TrancheSelection:
    """Select a diverse tranche without silently deleting valuable candidates.

    `preferred_size` and `family_quota` control scheduling, not corpus quality.
    If already-measured valuable candidates exceed the safety fuse, the stage
    fails closed for an explicit budget review instead of truncating them.
    """

    if preferred_size <= 0 or safety_fuse <= 0 or family_quota <= 0:
        raise ValueError("tranche limits must be positive")
    unique: list[tuple[int, dict[str, Any]]] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for index, case in enumerate(cases):
        key = exact_key(case)
        if key in seen:
            duplicates.append(str(case.get("name") or index))
            continue
        seen.add(key)
        unique.append((index, case))

    protected = [row for row in unique if value_signals(row[1])]
    if len(protected) > safety_fuse:
        report = {
            "input_cases": len(cases),
            "unique_cases": len(unique),
            "protected_cases": len(protected),
            "safety_fuse": safety_fuse,
            "decision": "budget_review_required_no_silent_truncation",
        }
        return TrancheSelection([], [case for _, case in unique], duplicates, True, report)

    target = min(safety_fuse, max(preferred_size, len(protected)))
    selected_indices = {index for index, _ in protected}
    selected = list(protected)
    grouped: defaultdict[str, deque[tuple[int, dict[str, Any]]]] = defaultdict(deque)
    for row in unique:
        if row[0] not in selected_indices:
            grouped[family_key(row[1])].append(row)
    family_counts: defaultdict[str, int] = defaultdict(int)
    ordered = sorted(grouped)
    while len(selected) < target and any(grouped[key] for key in ordered):
        progressed = False
        for key in ordered:
            if len(selected) >= target:
                break
            if grouped[key] and family_counts[key] < family_quota:
                selected.append(grouped[key].popleft())
                family_counts[key] += 1
                progressed = True
        if not progressed:
            break
    selected_indices = {index for index, _ in selected}
    selected.sort(key=lambda row: row[0])
    deferred = [case for index, case in unique if index not in selected_indices]
    report = {
        "input_cases": len(cases),
        "unique_cases": len(unique),
        "duplicates_removed": len(duplicates),
        "selected_cases": len(selected),
        "deferred_cases": len(deferred),
        "preferred_size": preferred_size,
        "safety_fuse": safety_fuse,
        "family_quota_per_tranche": family_quota,
        "protected_cases": len(protected),
        "decision": "selected_diverse_tranche",
    }
    return TrancheSelection(
        [case for _, case in selected], deferred, duplicates, False, report
    )


def assert_suite_fuse(
    retained_cases: list[dict[str, Any]], *, safety_fuse: int
) -> dict[str, Any]:
    """Fail closed when valuable retained cases exceed the emergency fuse."""

    valuable = [case for case in retained_cases if value_signals(case)]
    tripped = len(retained_cases) > safety_fuse
    return {
        "retained_cases": len(retained_cases),
        "valuable_cases": len(valuable),
        "safety_fuse": safety_fuse,
        "fuse_tripped": tripped,
        "action": (
            "budget_review_required_no_silent_truncation"
            if tripped
            else "within_safety_fuse"
        ),
    }
