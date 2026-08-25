from __future__ import annotations

from dataclasses import dataclass

from .policy import Observation


@dataclass(frozen=True)
class AdaptiveTranchePolicy:
    initial_size: int = 64
    minimum_size: int = 16
    maximum_size: int = 256
    high_yield_ratio: float = 0.25
    low_yield_ratio: float = 0.05
    growth_factor: float = 1.5
    shrink_factor: float = 0.5
    breadth_bootstrap_iterations: int = 1
    breadth_bootstrap_views: tuple[str, ...] = (
        "successful_workflow",
        "boundary_and_error",
        "fixture_and_unicode",
        "state_and_interaction",
    )
    reservoir_rerank_period: int = 3

    def __post_init__(self) -> None:
        if not (0 < self.minimum_size <= self.initial_size <= self.maximum_size):
            raise ValueError("invalid adaptive tranche bounds")
        if not (0 <= self.low_yield_ratio < self.high_yield_ratio <= 1):
            raise ValueError("invalid adaptive yield thresholds")
        if self.breadth_bootstrap_iterations < 0:
            raise ValueError("breadth_bootstrap_iterations must be nonnegative")
        if not self.breadth_bootstrap_views:
            raise ValueError("breadth_bootstrap_views must not be empty")
        if len(self.breadth_bootstrap_views) > 8:
            raise ValueError("breadth_bootstrap_views supports at most eight views")
        if self.reservoir_rerank_period < 1:
            raise ValueError("reservoir_rerank_period must be positive")

    def next_size(self, observations: list[Observation]) -> int:
        if not observations:
            return self.initial_size
        latest = observations[-1]
        denominator = max(1, latest.generated_candidates)
        valuable = latest.strong_novelty_total()
        ratio = valuable / denominator
        current = max(
            self.minimum_size,
            min(self.maximum_size, latest.generated_candidates or self.initial_size),
        )
        if ratio >= self.high_yield_ratio:
            return min(self.maximum_size, max(current + 1, round(current * self.growth_factor)))
        if ratio <= self.low_yield_ratio:
            return max(self.minimum_size, round(current * self.shrink_factor))
        return current
