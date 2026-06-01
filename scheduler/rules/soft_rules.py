"""Soft priority rules."""

from __future__ import annotations

from scheduler.models import BusState, ScheduleContext, driver_shift_end_minutes
from scheduler.rules.base import SoftRule


class IndividualWaitRule(SoftRule):
    """Score by how long this bus has waited at the current station."""

    name = "individual_wait"

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        return max(0.0, context.current_time - bus_state.station_arrival_time)


class OperatorFairnessRule(SoftRule):
    """Boost operators whose completed buses have accumulated higher waits."""

    name = "operator_fairness"

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        op_config = context.scenario.get_operator(bus_state.bus.operator)
        op_weight = op_config.weight if op_config else 1.0
        delays = context.get_operator_delays(bus_state.bus.operator)
        avg_delay = sum(delays) / len(delays) if delays else 0.0
        return op_weight * avg_delay


class OverallThroughputRule(SoftRule):
    """Prioritise buses with more downstream travel time remaining."""

    name = "overall_throughput"

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        remaining_km = context.get_remaining_distance(bus_state)
        speed = context.scenario.physics.travel_speed_kmh
        return (remaining_km / speed) * 60.0


class DriverShiftProximityRule(SoftRule):
    """Increase urgency when remaining journey approaches shift end."""

    name = "shift_proximity"

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        if bus_state.bus.driver_shift is None:
            return 0.0
        shift_end = driver_shift_end_minutes(bus_state.bus.driver_shift)
        shift_remaining = shift_end - context.current_time
        journey_remaining = (
            context.get_remaining_distance(bus_state)
            / context.scenario.physics.travel_speed_kmh
        ) * 60.0
        if shift_remaining <= 0 or journey_remaining <= 0:
            return 300.0
        urgency_ratio = journey_remaining / max(shift_remaining, 1.0)
        return max(0.0, (urgency_ratio - 0.5) * 200.0)


# Template for adding a new soft queue-priority rule:
#
# class ElectricityCostRule(SoftRule):
#     """Example: prefer charging during cheaper time windows."""
#
#     name = "electricity_cost"
#
#     def score(self, bus_state: BusState, context: ScheduleContext) -> float:
#         if 0 <= context.time_of_day < 6 * 60:
#             return 100.0
#         return 0.0
#
# To activate it, register it in scheduler.engine.run_simulation():
#
# scorer = WeightedScorer(
#     weights=scenario.weights,
#     rules=[
#         ...,
#         (ElectricityCostRule(), "electricity"),
#     ],
# )
#
# Then add an `electricity` field with a default to scheduler.models.Weights
# and set `weights.electricity` in scenario YAML when a scenario needs it.
