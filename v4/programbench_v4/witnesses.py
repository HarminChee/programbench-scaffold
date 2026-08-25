from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping


PREFIX_WEIGHT = {
    "coverage": 8.0,
    "state": 6.0,
    "error": 5.0,
    "behavior": 4.0,
    "assertion": 3.0,
    "fixture": 2.0,
}


@dataclass(frozen=True)
class WitnessCase:
    case_id: str
    witnesses: frozenset[str]
    original_index: int


@dataclass(frozen=True)
class ReplacementResult:
    selected_case_ids: tuple[str, ...]
    rejected_case_ids: tuple[str, ...]
    universe: frozenset[str]
    covered: frozenset[str]
    mandatory_case_ids: tuple[str, ...]
    blocked_reason: str | None

    @property
    def preserves_all_witnesses(self) -> bool:
        return self.blocked_reason is None and self.covered == self.universe


@dataclass(frozen=True)
class AdaptiveCapacityResult:
    """Auditable decision for a witness-density-adjusted safety fuse.

    ``hard_cap`` remains the emergency resource boundary.  The adaptive cap is
    allowed to grow only far enough to represent the exact witness universe
    plus configured headroom; it never authorizes dropping a witness.
    """

    replacement: ReplacementResult
    base_cap: int
    hard_cap: int
    effective_cap: int
    minimum_required_cap: int | None
    witness_count: int
    candidate_count: int
    mandatory_case_count: int
    mean_witnesses_per_case: float
    witness_density: float
    attempted_caps: tuple[int, ...]
    expanded: bool
    blocked_reason: str | None


def _weight(witness: str) -> float:
    return PREFIX_WEIGHT.get(witness.split(":", 1)[0], 1.0)


def witness_preserving_replacement(
    cases: Iterable[WitnessCase], *, cap: int, minimum_cases: int = 0
) -> ReplacementResult:
    """Select a deterministic bounded suite without losing any exact witness.

    The cap is a circuit breaker, never a truncation instruction. If the full
    witness universe cannot be represented within ``cap``, the result is
    blocked and callers must preserve the input suite/reservoir unchanged.
    """

    ordered = tuple(cases)
    if cap <= 0:
        raise ValueError("cap must be positive")
    if minimum_cases < 0 or minimum_cases > cap:
        raise ValueError("minimum_cases must be between zero and cap")
    ids = [case.case_id for case in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("case IDs must be unique")
    if any(not case.witnesses for case in ordered):
        raise ValueError("every case must carry at least one witness")

    universe = frozenset().union(*(case.witnesses for case in ordered))
    providers: dict[str, list[WitnessCase]] = {}
    for case in ordered:
        for witness in case.witnesses:
            providers.setdefault(witness, []).append(case)

    mandatory = {
        provider[0].case_id for provider in providers.values() if len(provider) == 1
    }
    if len(mandatory) > cap:
        return ReplacementResult(
            (), tuple(ids), universe, frozenset(), tuple(sorted(mandatory)),
            "mandatory_witness_providers_exceed_cap",
        )

    selected = {case.case_id for case in ordered if case.case_id in mandatory}
    covered = frozenset().union(
        *(case.witnesses for case in ordered if case.case_id in selected)
    ) if selected else frozenset()
    by_id = {case.case_id: case for case in ordered}

    while covered != universe and len(selected) < cap:
        uncovered = universe - covered
        choices = [case for case in ordered if case.case_id not in selected]
        best = max(
            choices,
            key=lambda case: (
                sum(
                    _weight(witness) / len(providers[witness])
                    for witness in case.witnesses & uncovered
                ),
                len(case.witnesses & uncovered),
                len(case.witnesses),
                -case.original_index,
            ),
        )
        if not (best.witnesses & uncovered):
            break
        selected.add(best.case_id)
        covered = covered | best.witnesses

    if covered != universe:
        return ReplacementResult(
            (), tuple(ids), universe, covered, tuple(sorted(mandatory)),
            "witness_universe_exceeds_cap",
        )

    # Retain a modest amount of non-essential diversity when the exact cover is
    # very small. This is deterministic and never displaces a witness provider.
    target = min(cap, max(minimum_cases, len(selected)))
    while len(selected) < target:
        remaining = [case for case in ordered if case.case_id not in selected]
        if not remaining:
            break
        best = max(
            remaining,
            key=lambda case: (
                sum(_weight(w) / len(providers[w]) for w in case.witnesses),
                len(case.witnesses),
                -case.original_index,
            ),
        )
        selected.add(best.case_id)

    selected_ids = tuple(case.case_id for case in ordered if case.case_id in selected)
    rejected_ids = tuple(case.case_id for case in ordered if case.case_id not in selected)
    final_covered = frozenset().union(*(by_id[item].witnesses for item in selected_ids))
    return ReplacementResult(
        selected_ids,
        rejected_ids,
        universe,
        final_covered,
        tuple(sorted(mandatory)),
        None,
    )


def adaptive_witness_capacity(
    cases: Iterable[WitnessCase],
    *,
    base_cap: int,
    hard_cap: int,
    headroom_ratio: float = 0.20,
    minimum_growth: int = 8,
    minimum_cases: int = 0,
) -> AdaptiveCapacityResult:
    """Find the smallest safe exact-witness cover and derive a bounded fuse.

    The search is deterministic.  It first probes geometrically up to the hard
    emergency ceiling, then binary-searches the minimum feasible cap.  The
    operational fuse includes bounded headroom so that a dense next tranche
    does not immediately cause another capacity audit.  If even ``hard_cap``
    cannot represent the universe, the result is blocked and callers must roll
    back to the last verified suite.
    """

    rows = tuple(cases)
    if base_cap <= 0 or hard_cap < base_cap:
        raise ValueError("adaptive caps require 0 < base_cap <= hard_cap")
    if not 0.0 <= headroom_ratio <= 1.0:
        raise ValueError("headroom_ratio must be in [0, 1]")
    if minimum_growth <= 0:
        raise ValueError("minimum_growth must be positive")
    if minimum_cases < 0 or minimum_cases > hard_cap:
        raise ValueError("minimum_cases must be between zero and hard_cap")
    if not rows:
        raise ValueError("adaptive capacity requires at least one case")

    universe = frozenset().union(*(row.witnesses for row in rows))
    provider_counts: dict[str, int] = {}
    for row in rows:
        for witness in row.witnesses:
            provider_counts[witness] = provider_counts.get(witness, 0) + 1
    mandatory_count = sum(
        1
        for row in rows
        if any(provider_counts[witness] == 1 for witness in row.witnesses)
    )
    mean_witnesses = sum(len(row.witnesses) for row in rows) / len(rows)
    density = len(universe) / max(1, len(rows))

    cache: dict[int, ReplacementResult] = {}
    attempted: list[int] = []

    def probe(cap: int) -> ReplacementResult:
        if cap not in cache:
            attempted.append(cap)
            cache[cap] = witness_preserving_replacement(
                rows, cap=cap, minimum_cases=min(minimum_cases, cap)
            )
        return cache[cap]

    initial = probe(base_cap)
    if initial.preserves_all_witnesses:
        return AdaptiveCapacityResult(
            initial, base_cap, hard_cap, base_cap, base_cap, len(universe),
            len(rows), mandatory_count, mean_witnesses, density,
            tuple(attempted), False, None,
        )

    lower = base_cap
    upper: int | None = None
    cursor = base_cap
    while cursor < hard_cap:
        cursor = min(
            hard_cap,
            max(cursor + minimum_growth, int(math.ceil(cursor * (1.0 + headroom_ratio)))),
        )
        result = probe(cursor)
        if result.preserves_all_witnesses:
            upper = cursor
            break
        lower = cursor

    if upper is None:
        blocked = cache[hard_cap]
        return AdaptiveCapacityResult(
            blocked, base_cap, hard_cap, hard_cap, None, len(universe),
            len(rows), mandatory_count, mean_witnesses, density,
            tuple(attempted), hard_cap > base_cap,
            "witness_universe_exceeds_hard_cap",
        )

    while upper - lower > 1:
        middle = (lower + upper) // 2
        if probe(middle).preserves_all_witnesses:
            upper = middle
        else:
            lower = middle
    minimum_required = upper
    effective = min(
        hard_cap,
        max(base_cap, int(math.ceil(minimum_required * (1.0 + headroom_ratio)))),
    )
    final = probe(effective)
    if not final.preserves_all_witnesses:
        raise RuntimeError("adaptive witness-capacity search produced an unsafe result")
    return AdaptiveCapacityResult(
        final, base_cap, hard_cap, effective, minimum_required, len(universe),
        len(rows), mandatory_count, mean_witnesses, density,
        tuple(attempted), effective > base_cap, None,
    )


def validate_witness_map(
    case_ids: Iterable[str], witness_map: Mapping[str, Iterable[str]]
) -> tuple[WitnessCase, ...]:
    ordered_ids = tuple(case_ids)
    if set(ordered_ids) != set(witness_map):
        missing = sorted(set(ordered_ids) - set(witness_map))
        extra = sorted(set(witness_map) - set(ordered_ids))
        raise ValueError(f"witness map scope mismatch: missing={missing}, extra={extra}")
    return tuple(
        WitnessCase(case_id, frozenset(witness_map[case_id]), index)
        for index, case_id in enumerate(ordered_ids)
    )
