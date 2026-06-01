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
        weighted_rules = [
            (rule, getattr(self.weights, weight_key, 1.0))
            for rule, weight_key in self.rules
        ]
        total_weight = sum(weight for _rule, weight in weighted_rules)
        if total_weight <= 0:
            return 0.0
        rule_total = sum(
            (weight / total_weight) * rule.score(bus_state, context)
            for rule, weight in weighted_rules
        )
        return bus_state.bus.weight * rule_total
