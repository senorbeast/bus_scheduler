"""Hard charging-plan rules."""

from __future__ import annotations

from scheduler.models import RoutePositions
from scheduler.rules.base import HardRule


class RangeConstraint(HardRule):
    """Ensure no travel gap exceeds battery range."""

    name = "range_constraint"

    def is_satisfied(
        self, stations: list[str], rp: RoutePositions, battery_range: float
    ) -> bool:
        checkpoints = [0.0] + [rp.positions[station] for station in stations] + [
            rp.total_distance
        ]
        return all(
            checkpoints[index + 1] - checkpoints[index] <= battery_range
            for index in range(len(checkpoints) - 1)
        )


class StationOrderConstraint(HardRule):
    """Ensure selected stations appear in forward route order."""

    name = "station_order"

    def is_satisfied(
        self, stations: list[str], rp: RoutePositions, battery_range: float
    ) -> bool:
        distances = [rp.positions[station] for station in stations]
        return distances == sorted(distances)


# Template for adding a new hard charging-plan constraint:
#
# class MaxStopsConstraint(HardRule):
#     """Example: reject candidate plans with too many en-route stops."""
#
#     name = "max_stops"
#
#     def is_satisfied(
#         self, stations: list[str], rp: RoutePositions, battery_range: float
#     ) -> bool:
#         return len(stations) <= 3
#
# To activate it, add an instance to the planner-side rule list in
# scheduler.planner.get_valid_charging_plans(). Hard rules are intentionally
# enforced before simulation, while soft rules arbitrate live charger queues.
