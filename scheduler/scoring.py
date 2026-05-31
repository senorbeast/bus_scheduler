"""Weighted soft-rule scorer."""

from __future__ import annotations

from scheduler.models import BusState, ScheduleContext, Weights
from scheduler.rules.base import SoftRule


class WeightedScorer:
    """Combine registered soft rules into one priority score."""

    def __init__(self, weights: Weights, rules: list[tuple[SoftRule, str]]) -> None:
        self.weights = weights
        self.rules = rules

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        override = context.priority_overrides.get(bus_state.bus.id, 1.0)
        rule_total = sum(
            getattr(self.weights, weight_key, 1.0) * rule.score(bus_state, context)
            for rule, weight_key in self.rules
        )
        return override * bus_state.bus.weight * rule_total
