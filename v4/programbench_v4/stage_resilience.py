"""Small fail-closed helpers for recoverable V4 stage output defects.

These helpers deliberately contain no repository or subprocess policy.  They
only make semantic retries, case identity, and failed-test mapping consistent
between adapters and the oracle bundle generator.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any, TypeVar


T = TypeVar("T")


def canonical_case_name(value: object) -> str:
    """Return the exact case-name form persisted by the oracle generator."""

    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_").lower()
    return cleaned or "case"


def canonicalize_case_identities(
    cases: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Canonicalize persisted identities and fail closed on any collision."""

    normalized: list[dict[str, Any]] = []
    renamed: dict[str, str] = {}
    seen: set[str] = set()
    for raw in cases:
        case = dict(raw)
        prior = str(case.get("name") or "case")
        current = canonical_case_name(prior)
        if current in seen:
            raise ValueError(f"canonical case identity collision: {current}")
        seen.add(current)
        case["name"] = current
        normalized.append(case)
        if current != prior:
            renamed[prior] = current
    return normalized, renamed


def run_semantic_retry(
    producer: Callable[[int, tuple[str, ...]], Any],
    validator: Callable[[Any], T],
    *,
    maximum_attempts: int = 2,
) -> tuple[T, tuple[str, ...]]:
    """Retry malformed stage payloads while preserving validation evidence.

    ``producer`` receives a one-based attempt number and previous validation
    errors, allowing the caller to give precise repair feedback.  Only
    ``ValueError`` from the validator is retryable; transport and
    infrastructure exceptions remain fatal.
    """

    if maximum_attempts < 1:
        raise ValueError("maximum_attempts must be positive")
    errors: list[str] = []
    for attempt in range(1, maximum_attempts + 1):
        payload = producer(attempt, tuple(errors))
        try:
            return validator(payload), tuple(errors)
        except ValueError as exc:
            errors.append(str(exc))
    raise ValueError(
        f"stage payload remained invalid after {maximum_attempts} attempts: "
        + "; ".join(errors)
    )


_GENERATED_TEST_INDEX = re.compile(r"(?:^|\.)test_(\d{4})_")


def failed_test_indexes(test_names: Iterable[object]) -> tuple[int, ...]:
    """Extract deterministic manifest indexes from generated pytest names."""

    indexes: set[int] = set()
    for value in test_names:
        match = _GENERATED_TEST_INDEX.search(str(value))
        if match:
            indexes.add(int(match.group(1)))
    return tuple(sorted(indexes))


def case_names_for_indexes(
    indexes: Iterable[int], manifest_cases: list[dict[str, Any]], *, reason: str
) -> tuple[str, ...]:
    """Map generated-test indexes back to stable candidate identities."""

    names: set[str] = set()
    for index in indexes:
        if index < 0 or index >= len(manifest_cases):
            raise ValueError(f"{reason} index is outside captured manifest: {index}")
        name = str(manifest_cases[index].get("name") or "")
        if not name:
            raise ValueError(f"captured {reason} case has no stable name: {index}")
        names.add(name)
    return tuple(sorted(names))


def repeat_failure_case_names(
    report: dict[str, Any], manifest_cases: list[dict[str, Any]]
) -> tuple[str, ...]:
    """Return cases whose deterministic repeat failed in the quality report."""

    repeat = report.get("repeat_check") or {}
    junit = repeat.get("junit_summary") or {}
    indexes = failed_test_indexes(junit.get("failed_test_names") or [])
    return case_names_for_indexes(indexes, manifest_cases, reason="repeat failure")
