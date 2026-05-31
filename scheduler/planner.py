"""Charging plan generation."""

from __future__ import annotations

from itertools import combinations

from scheduler.models import Bus, RoutePositions, Scenario
from scheduler.rules.hard_rules import RangeConstraint, StationOrderConstraint


def get_valid_charging_plans(bus: Bus, scenario: Scenario) -> list[list[str]]:
    """Enumerate valid en-route charging plans for one bus."""
    direction = bus.direction or scenario.route.get_direction(bus.origin_node, bus.destination_node)
    positions = scenario.route.get_node_positions(direction)
    trip_distance = scenario.route.get_distance_between(
        bus.origin_node, bus.destination_node, direction
    )
    station_ids = scenario.route.get_station_ids_between(
        bus.origin_node, bus.destination_node
    )
    battery = scenario.physics.battery_range_km
    rp = RoutePositions(
        positions={
            station_id: positions[station_id] - positions[bus.origin_node]
            for station_id in [bus.origin_node, *station_ids, bus.destination_node]
        },
        total_distance=trip_distance,
    )
    rules = [StationOrderConstraint(), RangeConstraint()]
    valid: list[list[str]] = []
    for size in range(0, len(station_ids) + 1):
        for combo in combinations(station_ids, size):
            plan = list(combo)
            if all(rule.is_satisfied(plan, rp, battery) for rule in rules):
                valid.append(plan)
    return valid


def select_charging_plan(bus_index: int, bus: Bus, scenario: Scenario) -> list[str]:
    """Select a minimum-stop valid plan, round-robin across equivalent plans."""
    valid = get_valid_charging_plans(bus, scenario)
    if not valid:
        raise ValueError(
            f"No valid charging plan for bus '{bus.id}' from "
            f"{bus.origin_node} to {bus.destination_node}."
        )
    min_stops = min(len(plan) for plan in valid)
    candidates = [plan for plan in valid if len(plan) == min_stops]
    return candidates[bus_index % len(candidates)]
