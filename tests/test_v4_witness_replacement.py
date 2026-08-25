from __future__ import annotations

import pytest

from v4.programbench_v4.witnesses import (
    adaptive_witness_capacity,
    WitnessCase,
    validate_witness_map,
    witness_preserving_replacement,
)


def case(name: str, *witnesses: str, index: int) -> WitnessCase:
    return WitnessCase(name, frozenset(witnesses), index)


def test_replacement_preserves_coverage_and_behavior_witnesses() -> None:
    result = witness_preserving_replacement(
        [
            case("a", "coverage:u1", "behavior:b1", index=0),
            case("b", "coverage:u1", "behavior:b1", index=1),
            case("c", "coverage:u2", "behavior:b1", index=2),
            case("d", "coverage:u1", "behavior:b2", index=3),
        ],
        cap=3,
    )
    assert result.preserves_all_witnesses
    assert set(result.selected_case_ids) == {"c", "d"}
    assert result.rejected_case_ids == ("a", "b")


def test_same_output_cases_with_different_coverage_are_both_preserved() -> None:
    result = witness_preserving_replacement(
        [
            case("left", "behavior:same", "coverage:left", index=0),
            case("right", "behavior:same", "coverage:right", index=1),
        ],
        cap=2,
    )
    assert result.selected_case_ids == ("left", "right")
    assert result.preserves_all_witnesses


def test_cap_blocks_instead_of_silently_losing_witnesses() -> None:
    result = witness_preserving_replacement(
        [
            case("a", "coverage:a", index=0),
            case("b", "coverage:b", index=1),
            case("c", "coverage:c", index=2),
        ],
        cap=2,
    )
    assert result.blocked_reason == "mandatory_witness_providers_exceed_cap"
    assert result.selected_case_ids == ()


def test_minimum_diversity_fill_is_deterministic() -> None:
    rows = [
        case("a", "coverage:shared", index=0),
        case("b", "coverage:shared", index=1),
        case("c", "coverage:shared", index=2),
    ]
    first = witness_preserving_replacement(rows, cap=3, minimum_cases=2)
    second = witness_preserving_replacement(rows, cap=3, minimum_cases=2)
    assert first == second
    assert first.selected_case_ids == ("a", "b")


def test_witness_map_scope_must_match_cases_exactly() -> None:
    with pytest.raises(ValueError, match="scope mismatch"):
        validate_witness_map(["a", "b"], {"a": ["coverage:a"]})


def test_adaptive_capacity_grows_only_enough_to_preserve_witnesses() -> None:
    rows = [case(f"case-{index}", f"coverage:{index}", index=index) for index in range(65)]
    decision = adaptive_witness_capacity(
        rows, base_cap=64, hard_cap=128, headroom_ratio=0.20, minimum_growth=8
    )
    assert decision.replacement.preserves_all_witnesses
    assert decision.minimum_required_cap == 65
    assert decision.effective_cap == 78
    assert decision.expanded is True
    assert len(decision.replacement.selected_case_ids) == 65
    assert decision.witness_count == 65


def test_adaptive_capacity_hard_cap_never_drops_a_witness() -> None:
    rows = [case(f"case-{index}", f"coverage:{index}", index=index) for index in range(9)]
    decision = adaptive_witness_capacity(
        rows, base_cap=4, hard_cap=8, headroom_ratio=0.25, minimum_growth=2
    )
    assert decision.replacement.preserves_all_witnesses is False
    assert decision.blocked_reason == "witness_universe_exceeds_hard_cap"
    assert decision.minimum_required_cap is None


def test_adaptive_capacity_is_deterministic() -> None:
    rows = [
        case("a", "coverage:1", "behavior:x", index=0),
        case("b", "coverage:2", "behavior:x", index=1),
        case("c", "coverage:3", "behavior:y", index=2),
        case("d", "coverage:1", "behavior:y", index=3),
    ]
    first = adaptive_witness_capacity(rows, base_cap=2, hard_cap=4, minimum_growth=1)
    second = adaptive_witness_capacity(rows, base_cap=2, hard_cap=4, minimum_growth=1)
    assert first == second
