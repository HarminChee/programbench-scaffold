from __future__ import annotations

import pytest

from v4.programbench_v4.candidates import assert_suite_fuse, select_tranche
from v4.programbench_v4.policy import MarginalPolicy, Observation
from v4.programbench_v4.scheduler import AdaptiveTranchePolicy


def observation(coverage: float, novelty: int, *, scope: str = "a" * 64) -> Observation:
    return Observation(
        primary_coverage=coverage,
        coverage_scope_sha256=scope,
        retained_cases=20,
        generated_candidates=20,
        wall_seconds=10,
        new_behavior_families=novelty,
    )


def test_stops_only_after_multidimensional_marginal_saturation() -> None:
    policy = MarginalPolicy(saturation_windows=2, minimum_promoted_observations=3)
    decision = policy.decide(
        [observation(40, 4), observation(40.1, 0), observation(40.2, 0)],
        total_raw_candidates=60,
        retained_suite_cases=25,
        iteration=3,
    )
    assert decision.successful
    assert decision.reason == "marginal_saturation"


def test_rust_auxiliary_path_requires_two_consecutive_sub_one_percent_gains() -> None:
    policy = MarginalPolicy(
        saturation_windows=2,
        minimum_promoted_observations=3,
        require_auxiliary_path_saturation=True,
        auxiliary_path_saturation_windows=2,
        auxiliary_path_minimum_relative_gain=0.01,
    )

    def row(coverage: float, path: int) -> Observation:
        return Observation(
            primary_coverage=coverage,
            coverage_scope_sha256="a" * 64,
            retained_cases=20,
            generated_candidates=8,
            wall_seconds=1,
            auxiliary_path_coverage=path,
            auxiliary_path_valid=True,
        )

    decision = policy.decide(
        [row(50.0, 100), row(50.1, 100), row(50.2, 100)],
        total_raw_candidates=24,
        retained_suite_cases=20,
        iteration=3,
    )
    assert decision.reason == "marginal_saturation"
    assert decision.evidence["recent_auxiliary_path_relative_gains"] == [0.0, 0.0]

    growing = policy.decide(
        [row(50.0, 100), row(50.1, 100), row(50.2, 102)],
        total_raw_candidates=24,
        retained_suite_cases=20,
        iteration=3,
    )
    assert growing.continue_refinement is True
    assert growing.reason == "auxiliary_path_value_remaining"
    assert growing.evidence["auxiliary_path_edge_used_for_stop"] is False

    unstable = policy.decide(
        [row(50.0, 100), row(50.1, 99), row(50.2, 98)],
        total_raw_candidates=24,
        retained_suite_cases=20,
        iteration=3,
    )
    assert unstable.continue_refinement is True
    assert unstable.reason == "auxiliary_path_non_monotonic"


def test_rust_auxiliary_path_missing_measurement_requests_evidence() -> None:
    policy = MarginalPolicy(
        saturation_windows=2,
        minimum_promoted_observations=3,
        require_auxiliary_path_saturation=True,
    )
    rows = [observation(50.0, 0), observation(50.1, 0), observation(50.2, 0)]
    decision = policy.decide(
        rows,
        total_raw_candidates=24,
        retained_suite_cases=20,
        iteration=3,
    )
    assert decision.continue_refinement is True
    assert decision.reason == "need_auxiliary_path_saturation_evidence"


def test_behavior_novelty_continues_even_without_coverage_gain() -> None:
    policy = MarginalPolicy(saturation_windows=2, minimum_promoted_observations=3)
    decision = policy.decide(
        [observation(40, 4), observation(40.1, 0), observation(40.1, 1)],
        total_raw_candidates=60,
        retained_suite_cases=25,
        iteration=3,
    )
    assert decision.continue_refinement
    assert decision.reason == "marginal_value_remaining"


def test_weak_fixture_and_assertion_novelty_cannot_block_saturation() -> None:
    policy = MarginalPolicy(saturation_windows=2, minimum_promoted_observations=3)
    rows = [
        observation(40.0, 0),
        Observation(
            primary_coverage=40.1,
            coverage_scope_sha256="a" * 64,
            retained_cases=20,
            generated_candidates=20,
            wall_seconds=10,
            new_fixture_shapes=8,
            new_assertion_classes=4,
        ),
        Observation(
            primary_coverage=40.2,
            coverage_scope_sha256="a" * 64,
            retained_cases=20,
            generated_candidates=20,
            wall_seconds=10,
            new_fixture_shapes=5,
            new_assertion_classes=2,
        ),
    ]
    decision = policy.decide(
        rows,
        total_raw_candidates=60,
        retained_suite_cases=25,
        iteration=3,
    )
    assert decision.successful
    assert decision.reason == "marginal_saturation"
    assert decision.evidence["recent_strong_novelty"] == [0, 0]
    assert decision.evidence["recent_weak_novelty"] == [12, 7]


def test_budget_is_an_unsuccessful_fuse_not_a_quality_stop() -> None:
    policy = MarginalPolicy(
        saturation_windows=2,
        minimum_promoted_observations=3,
        raw_candidate_fuse=50,
    )
    decision = policy.decide(
        [observation(20, 5), observation(25, 5), observation(30, 5)],
        total_raw_candidates=50,
        retained_suite_cases=25,
        iteration=3,
    )
    assert not decision.successful
    assert decision.reason == "budget_fuse_with_remaining_marginal_value"


def test_raw_fuse_expands_only_for_high_exact_witness_density() -> None:
    policy = MarginalPolicy(
        raw_candidate_fuse=1000,
        adaptive_raw_fuse=True,
        raw_candidate_hard_fuse=1200,
        raw_fuse_headroom_ratio=0.20,
        raw_fuse_minimum_growth=100,
        raw_fuse_minimum_witness_density=0.25,
        raw_fuse_minimum_promoted_observations=3,
    )
    high = [observation(20 + index, 8) for index in range(3)]
    evidence = policy.raw_fuse_evidence(high)
    assert evidence["eligible_for_expansion"]
    assert evidence["effective_limit"] == 1200
    assert evidence["at_hard_limit"]
    assert evidence["new_exact_witnesses"] == 24
    assert evidence["generated_candidates_in_promoted_observations"] == 60

    low = [observation(20 + index, 1) for index in range(3)]
    evidence = policy.raw_fuse_evidence(low)
    assert not evidence["eligible_for_expansion"]
    assert evidence["effective_limit"] == 1000


def test_adaptive_raw_hard_fuse_is_incomplete_not_success() -> None:
    policy = MarginalPolicy(
        raw_candidate_fuse=1000,
        adaptive_raw_fuse=True,
        raw_candidate_hard_fuse=1200,
        raw_fuse_minimum_witness_density=0.25,
        raw_fuse_minimum_promoted_observations=3,
    )
    rows = [observation(20 + index, 120) for index in range(3)]
    decision = policy.decide(
        rows,
        total_raw_candidates=1200,
        retained_suite_cases=60,
        iteration=4,
    )
    assert decision.terminal and not decision.successful
    assert decision.reason == "budget_fuse_with_remaining_marginal_value"
    assert decision.evidence["fuses"]["raw_evidence"]["at_hard_limit"]
    assert (
        decision.evidence["fuses"]["raw_evidence"]["density_denominator_source"]
        == "unique_persisted_raw_candidates"
    )


def test_adaptive_raw_fuse_requires_distinct_hard_limit() -> None:
    with pytest.raises(ValueError, match="requires"):
        MarginalPolicy(raw_candidate_fuse=1000, adaptive_raw_fuse=True)
    with pytest.raises(ValueError, match="must exceed"):
        MarginalPolicy(
            raw_candidate_fuse=1000,
            adaptive_raw_fuse=True,
            raw_candidate_hard_fuse=1000,
        )


def test_adaptive_suite_uses_hard_ceiling_for_terminal_budget() -> None:
    policy = MarginalPolicy(
        adaptive_retained_fuse=True,
        retained_suite_fuse=64,
        retained_suite_hard_fuse=128,
    )
    below = policy.decide(
        [observation(20, 5)],
        total_raw_candidates=20,
        retained_suite_cases=78,
        iteration=1,
    )
    assert below.continue_refinement
    assert below.reason == "need_more_evidence"
    at_hard = policy.decide(
        [observation(20, 5)],
        total_raw_candidates=20,
        retained_suite_cases=128,
        iteration=1,
    )
    assert at_hard.terminal and not at_hard.successful
    assert at_hard.reason == "budget_fuse_before_saturation_evidence"


def test_adaptive_suite_policy_requires_a_distinct_hard_ceiling() -> None:
    with pytest.raises(ValueError, match="requires"):
        MarginalPolicy(adaptive_retained_fuse=True, retained_suite_fuse=64)
    with pytest.raises(ValueError, match="must exceed"):
        MarginalPolicy(
            adaptive_retained_fuse=True,
            retained_suite_fuse=64,
            retained_suite_hard_fuse=64,
        )


def test_pilot_attempt_fuse_never_claims_success() -> None:
    policy = MarginalPolicy(pilot_iteration_fuse=2)
    decision = policy.decide(
        [observation(10, 1), observation(10.1, 0)],
        total_raw_candidates=10,
        retained_suite_cases=5,
        iteration=2,
    )
    assert decision.terminal and not decision.successful
    assert decision.reason == "pilot_safety_fuse"


def test_coverage_scope_change_cannot_be_called_saturation() -> None:
    policy = MarginalPolicy()
    decision = policy.decide(
        [
            observation(20, 0, scope="a" * 64),
            observation(20.1, 0, scope="a" * 64),
            observation(20.1, 0, scope="b" * 64),
        ],
        total_raw_candidates=30,
        retained_suite_cases=10,
        iteration=3,
    )
    assert decision.continue_refinement
    assert decision.reason == "coverage_scope_changed_need_new_baseline"


def test_same_scope_coverage_regression_fails_closed() -> None:
    policy = MarginalPolicy()
    decision = policy.decide(
        [observation(32.0, 4), observation(29.0, 4)],
        total_raw_candidates=30,
        retained_suite_cases=30,
        iteration=2,
    )
    assert decision.terminal and not decision.successful
    assert decision.reason == "coverage_non_monotonic"


def test_candidate_tranche_deferred_not_deleted() -> None:
    cases = [
        {"name": f"case-{index}", "area": f"area-{index % 2}", "args": [str(index)]}
        for index in range(12)
    ]
    result = select_tranche(cases, preferred_size=4, safety_fuse=10, family_quota=1)
    assert len(result.selected) == 4
    assert len(result.deferred) == 8
    assert not result.fuse_tripped


def test_valuable_candidates_are_not_silently_truncated() -> None:
    cases = [
        {
            "name": f"valuable-{index}",
            "args": [str(index)],
            "value_evidence": {"new_coverage": True},
        }
        for index in range(5)
    ]
    result = select_tranche(cases, preferred_size=2, safety_fuse=4, family_quota=1)
    assert result.fuse_tripped
    assert result.selected == []
    assert len(result.deferred) == 5
    assert assert_suite_fuse(cases, safety_fuse=4)["action"] == "budget_review_required_no_silent_truncation"


def test_adaptive_tranche_grows_and_shrinks() -> None:
    policy = AdaptiveTranchePolicy(initial_size=20, minimum_size=10, maximum_size=40)
    assert policy.next_size([]) == 20
    assert policy.next_size([observation(10, 10)]) == 30
    assert policy.next_size([observation(10, 0)]) == 10


def test_breadth_bootstrap_is_bounded_views_not_cartesian_batches(tmp_path) -> None:
    from v4.adapters.gofumpt_pilot_adapter import plan_tranche

    plan = plan_tranche(
        {
            "iteration": 1,
            "workflow_context": {
                "recommended_tranche_size": 48,
                "breadth_bootstrap_iterations": 1,
                "breadth_bootstrap_views": [
                    "successful_workflow",
                    "boundary_and_error",
                    "fixture_and_unicode",
                    "state_and_interaction",
                ],
            },
            "repository": {
                "behavior_themes": ["theme-a", "theme-b", "theme-c"],
            },
        },
        tmp_path,
    )
    assert plan["plan"]["breadth_bootstrap"] is True
    assert len(plan["plan"]["breadth_views"]) == 4
    assert plan["plan"]["requested_cases"] == 48
    assert plan["plan"]["requested_cases"] < 48 * 4
    assert plan["plan"]["theme_portfolio"] == ["theme-a", "theme-b"]


def test_go_never_inherits_native_auxiliary_path_stop() -> None:
    from v4.programbench_v4.controller import _policy_for_repository

    base = MarginalPolicy(require_auxiliary_path_saturation=True)
    assert not _policy_for_repository(base, {"language": "go"}).require_auxiliary_path_saturation
    assert _policy_for_repository(
        base, {"language": "rust", "afl_path_auxiliary_stop": True}
    ).require_auxiliary_path_saturation
