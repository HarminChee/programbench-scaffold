from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Iterable


@dataclass(frozen=True)
class Observation:
    """One promoted tranche, measured after quality filtering.

    Coverage is deliberately a generic primary percentage: Go reports statement
    coverage and Rust reports region coverage.  Novelty fields are counts added
    by this tranche, not cumulative totals.
    """

    primary_coverage: float
    coverage_scope_sha256: str
    retained_cases: int
    generated_candidates: int
    wall_seconds: float
    new_behavior_families: int = 0
    new_error_classes: int = 0
    new_fixture_shapes: int = 0
    new_assertion_classes: int = 0
    new_state_transitions: int = 0
    new_coverage_units: int = 0
    coverage_valid: bool = True
    quality_passed: bool = True
    promoted: bool = True
    covered_units: tuple[str, ...] = ()
    auxiliary_path_coverage: int | None = None
    auxiliary_path_valid: bool = False

    def strong_novelty_total(self) -> int:
        """Novelty that should keep refinement alive.

        Coverage units, behavior families, error classes and state
        transitions enlarge the executable behavior surface. Fixture and
        assertion variation remains useful evidence, but on its own must not
        prevent marginal saturation forever.
        """

        return (
            self.new_coverage_units
            + self.new_behavior_families
            + self.new_error_classes
            + self.new_state_transitions
        )

    def weak_novelty_total(self) -> int:
        return self.new_fixture_shapes + self.new_assertion_classes

    def novelty_total(self) -> int:
        return self.strong_novelty_total() + self.weak_novelty_total()


@dataclass(frozen=True)
class StopDecision:
    continue_refinement: bool
    reason: str
    terminal: bool
    successful: bool
    evidence: dict[str, Any]


@dataclass(frozen=True)
class MarginalPolicy:
    """Adaptive V4 stopping policy.

    There is intentionally no production maximum-round or fixed coverage
    target.  Optional pilot fuses bound an experiment but never turn an
    under-evaluated suite into a successful result.
    """

    saturation_windows: int = 2
    minimum_primary_gain_pp: float = 0.35
    minimum_novelty: int = 1
    minimum_strong_novelty: int | None = None
    minimum_promoted_observations: int = 3
    repo_wall_budget_seconds: float | None = None
    raw_candidate_fuse: int | None = None
    adaptive_raw_fuse: bool = False
    raw_candidate_hard_fuse: int | None = None
    raw_fuse_headroom_ratio: float = 0.20
    raw_fuse_minimum_growth: int = 64
    raw_fuse_minimum_witness_density: float = 0.25
    raw_fuse_minimum_promoted_observations: int = 3
    retained_suite_fuse: int | None = None
    adaptive_retained_fuse: bool = False
    retained_suite_hard_fuse: int | None = None
    retained_fuse_headroom_ratio: float = 0.20
    retained_fuse_minimum_growth: int = 8
    pilot_iteration_fuse: int | None = None
    require_auxiliary_path_saturation: bool = False
    auxiliary_path_saturation_windows: int = 2
    auxiliary_path_minimum_relative_gain: float = 0.01

    def __post_init__(self) -> None:
        if self.saturation_windows < 1:
            raise ValueError("saturation_windows must be positive")
        if self.auxiliary_path_saturation_windows < 1:
            raise ValueError("auxiliary_path_saturation_windows must be positive")
        if not 0.0 <= self.auxiliary_path_minimum_relative_gain <= 1.0:
            raise ValueError("auxiliary_path_minimum_relative_gain must be in [0, 1]")
        if self.minimum_promoted_observations < self.saturation_windows + 1:
            raise ValueError(
                "minimum_promoted_observations must include a baseline plus saturation windows"
            )
        for name in (
            "repo_wall_budget_seconds",
            "raw_candidate_fuse",
            "raw_candidate_hard_fuse",
            "retained_suite_fuse",
            "retained_suite_hard_fuse",
            "pilot_iteration_fuse",
        ):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when set")
        if not 0.0 <= self.retained_fuse_headroom_ratio <= 1.0:
            raise ValueError("retained_fuse_headroom_ratio must be in [0, 1]")
        if self.retained_fuse_minimum_growth <= 0:
            raise ValueError("retained_fuse_minimum_growth must be positive")
        if not 0.0 <= self.raw_fuse_headroom_ratio <= 1.0:
            raise ValueError("raw_fuse_headroom_ratio must be in [0, 1]")
        if self.raw_fuse_minimum_growth <= 0:
            raise ValueError("raw_fuse_minimum_growth must be positive")
        if self.raw_fuse_minimum_witness_density < 0.0:
            raise ValueError("raw_fuse_minimum_witness_density must be nonnegative")
        if self.raw_fuse_minimum_promoted_observations <= 0:
            raise ValueError("raw_fuse_minimum_promoted_observations must be positive")
        if self.adaptive_raw_fuse:
            if self.raw_candidate_fuse is None or self.raw_candidate_hard_fuse is None:
                raise ValueError(
                    "adaptive_raw_fuse requires raw_candidate_fuse and raw_candidate_hard_fuse"
                )
            if self.raw_candidate_hard_fuse <= self.raw_candidate_fuse:
                raise ValueError("raw_candidate_hard_fuse must exceed the base fuse")
        elif self.raw_candidate_hard_fuse is not None:
            raise ValueError("raw_candidate_hard_fuse requires adaptive_raw_fuse")
        if self.adaptive_retained_fuse:
            if self.retained_suite_fuse is None or self.retained_suite_hard_fuse is None:
                raise ValueError(
                    "adaptive_retained_fuse requires retained_suite_fuse and retained_suite_hard_fuse"
                )
            if self.retained_suite_hard_fuse <= self.retained_suite_fuse:
                raise ValueError("retained_suite_hard_fuse must exceed the base fuse")
        elif self.retained_suite_hard_fuse is not None:
            raise ValueError("retained_suite_hard_fuse requires adaptive_retained_fuse")

    def retained_emergency_limit(self) -> int | None:
        if self.adaptive_retained_fuse:
            return self.retained_suite_hard_fuse
        return self.retained_suite_fuse

    def raw_fuse_evidence(
        self,
        observations: Iterable[Observation],
        *,
        unique_persisted_raw_candidates: int | None = None,
    ) -> dict[str, Any]:
        promoted = [
            row
            for row in observations
            if row.promoted and row.quality_passed and row.coverage_valid
        ]
        generated = sum(max(0, row.generated_candidates) for row in promoted)
        # Capacity expansion must be justified by strong behavioral witnesses;
        # fixture/assertion churn alone is deliberately not enough.
        exact_witnesses = sum(max(0, row.strong_novelty_total()) for row in promoted)
        density_denominator = (
            max(0, unique_persisted_raw_candidates)
            if unique_persisted_raw_candidates is not None
            else generated
        )
        density = exact_witnesses / max(1, density_denominator)
        base = self.raw_candidate_fuse
        eligible = bool(
            self.adaptive_raw_fuse
            and base is not None
            and len(promoted) >= self.raw_fuse_minimum_promoted_observations
            and density >= self.raw_fuse_minimum_witness_density
        )
        effective = base
        if eligible and base is not None and self.raw_candidate_hard_fuse is not None:
            growth = max(
                self.raw_fuse_minimum_growth,
                math.ceil(base * self.raw_fuse_headroom_ratio),
            )
            effective = min(self.raw_candidate_hard_fuse, base + growth)
        return {
            "adaptive": self.adaptive_raw_fuse,
            "base_limit": base,
            "hard_limit": self.raw_candidate_hard_fuse,
            "effective_limit": effective,
            "expanded": effective is not None and base is not None and effective > base,
            "at_hard_limit": (
                effective is not None
                and self.raw_candidate_hard_fuse is not None
                and effective == self.raw_candidate_hard_fuse
            ),
            "promoted_observations": len(promoted),
            "generated_candidates_in_promoted_observations": generated,
            "density_denominator": density_denominator,
            "density_denominator_source": (
                "unique_persisted_raw_candidates"
                if unique_persisted_raw_candidates is not None
                else "promoted_tranche_attempts"
            ),
            "new_exact_witnesses": exact_witnesses,
            "exact_witness_density": density,
            "minimum_witness_density": self.raw_fuse_minimum_witness_density,
            "minimum_promoted_observations": self.raw_fuse_minimum_promoted_observations,
            "eligible_for_expansion": eligible,
        }

    def decide(
        self,
        observations: Iterable[Observation],
        *,
        total_raw_candidates: int,
        retained_suite_cases: int,
        iteration: int,
        quality_unrepairable: bool = False,
        infrastructure_blocked: bool = False,
    ) -> StopDecision:
        rows = list(observations)
        total_wall = sum(max(0.0, row.wall_seconds) for row in rows)
        common = {
            "iteration": iteration,
            "observations": [asdict(row) for row in rows],
            "total_wall_seconds": total_wall,
            "total_raw_candidates": total_raw_candidates,
            "retained_suite_cases": retained_suite_cases,
            "policy": asdict(self),
        }
        if infrastructure_blocked:
            return StopDecision(False, "infrastructure_blocked", True, False, common)
        if quality_unrepairable:
            return StopDecision(False, "quality_blocked", True, False, common)
        if (
            self.repo_wall_budget_seconds is not None
            and total_wall >= self.repo_wall_budget_seconds
        ):
            return StopDecision(False, "time_budget_exhausted", True, False, common)
        if self.pilot_iteration_fuse is not None and iteration >= self.pilot_iteration_fuse:
            return StopDecision(False, "pilot_safety_fuse", True, False, common)

        raw_fuse_evidence = self.raw_fuse_evidence(
            rows, unique_persisted_raw_candidates=total_raw_candidates
        )
        raw_limit = raw_fuse_evidence["effective_limit"]
        raw_fuse = raw_limit is not None and total_raw_candidates >= raw_limit
        retained_limit = self.retained_emergency_limit()
        suite_fuse = retained_limit is not None and retained_suite_cases >= retained_limit
        common["fuses"] = {
            "raw": raw_fuse,
            "raw_evidence": raw_fuse_evidence,
            "retained_suite": suite_fuse,
        }

        promoted = [
            row
            for row in rows
            if row.promoted and row.quality_passed and row.coverage_valid
        ]
        scopes = {row.coverage_scope_sha256 for row in promoted}
        if len(scopes) > 1:
            common["coverage_scope_sha256_values"] = sorted(scopes)
            return StopDecision(
                True, "coverage_scope_changed_need_new_baseline", False, False, common
            )
        comparable_gains = [
            current.primary_coverage - previous.primary_coverage
            for previous, current in zip(promoted, promoted[1:])
        ]
        common["all_primary_gains_pp"] = comparable_gains
        if any(gain < -1e-9 for gain in comparable_gains):
            # A retained union (or a witness-preserving replacement) must not
            # lose coverage under an identical denominator/binary scope.  A
            # regression therefore indicates rotating samples, stale data, or
            # an invalid replacement and must never enter saturation history.
            return StopDecision(
                False, "coverage_non_monotonic", True, False, common
            )
        if len(promoted) < self.minimum_promoted_observations:
            if raw_fuse or suite_fuse:
                return StopDecision(
                    False, "budget_fuse_before_saturation_evidence", True, False, common
                )
            return StopDecision(True, "need_more_evidence", False, False, common)

        recent = promoted[-self.saturation_windows :]
        starting = len(promoted) - len(recent)
        gains = [
            row.primary_coverage - promoted[starting + index - 1].primary_coverage
            for index, row in enumerate(recent)
        ]
        low_gain = all(gain < self.minimum_primary_gain_pp for gain in gains)
        strong_threshold = (
            self.minimum_novelty
            if self.minimum_strong_novelty is None
            else self.minimum_strong_novelty
        )
        low_strong_novelty = all(
            row.strong_novelty_total() < strong_threshold for row in recent
        )
        common["recent_primary_gains_pp"] = gains
        common["recent_novelty"] = [row.novelty_total() for row in recent]
        common["recent_strong_novelty"] = [row.strong_novelty_total() for row in recent]
        common["recent_weak_novelty"] = [row.weak_novelty_total() for row in recent]
        common["strong_novelty_threshold"] = strong_threshold
        common["weak_novelty_counts_for_saturation"] = False
        if low_gain and low_strong_novelty and self.require_auxiliary_path_saturation:
            measured_paths = [
                row
                for row in promoted
                if row.auxiliary_path_valid and row.auxiliary_path_coverage is not None
            ]
            needed = self.auxiliary_path_saturation_windows + 1
            common["auxiliary_path_metric"] = "distinct_path_signatures"
            common["auxiliary_path_edge_used_for_stop"] = False
            common["auxiliary_path_measurements"] = [
                int(row.auxiliary_path_coverage or 0) for row in measured_paths
            ]
            if len(measured_paths) < needed:
                common["auxiliary_path_measurements_needed"] = needed
                return StopDecision(
                    True, "need_auxiliary_path_saturation_evidence", False, False, common
                )
            path_values = [
                int(row.auxiliary_path_coverage or 0) for row in measured_paths[-needed:]
            ]
            path_gains = [
                (current - previous) / max(previous, 1)
                for previous, current in zip(path_values, path_values[1:])
            ]
            common["recent_auxiliary_path_values"] = path_values
            common["recent_auxiliary_path_relative_gains"] = path_gains
            common["auxiliary_path_relative_gain_threshold"] = (
                self.auxiliary_path_minimum_relative_gain
            )
            if any(gain < 0.0 for gain in path_gains):
                return StopDecision(
                    True, "auxiliary_path_non_monotonic", False, False, common
                )
            if not all(
                gain < self.auxiliary_path_minimum_relative_gain for gain in path_gains
            ):
                return StopDecision(
                    True, "auxiliary_path_value_remaining", False, False, common
                )
        if low_gain and low_strong_novelty:
            return StopDecision(False, "marginal_saturation", True, True, common)
        if raw_fuse or suite_fuse:
            return StopDecision(
                False, "budget_fuse_with_remaining_marginal_value", True, False, common
            )
        return StopDecision(True, "marginal_value_remaining", False, False, common)
