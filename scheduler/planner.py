"""Charging plan generation and network-aware assignment."""

from __future__ import annotations

from itertools import combinations
from typing import Dict, List, Tuple

from scheduler.models import Bus, RoutePositions, Scenario, time_str_to_minutes
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


def _get_direction(bus: Bus, scenario: Scenario) -> str:
    """Return the bus direction, deriving it from endpoints when omitted."""
    return bus.direction or scenario.route.get_direction(
        bus.origin_node, bus.destination_node
    )


def _get_positions_and_origin(
    bus: Bus,
    scenario: Scenario,
) -> Tuple[Dict[str, float], float]:
    """Return direction-relative positions and the bus origin's position."""
    direction = _get_direction(bus, scenario)
    positions = scenario.route.get_node_positions(direction)
    return positions, positions[bus.origin_node]


def _score_plan(
    bus: Bus,
    plan: List[str],
    pool: Dict[str, List[float]],
    speed: float,
    charge_time: int,
    scenario: Scenario,
) -> Tuple[float, float]:
    """Predict total wait and current station load for a plan without booking it."""
    positions, origin_pos = _get_positions_and_origin(bus, scenario)
    current_t = time_str_to_minutes(bus.departure)
    current_pos = origin_pos
    total_wait = 0.0

    for station_id in plan:
        travel_t = ((positions[station_id] - current_pos) / speed) * 60.0
        arrival = current_t + travel_t
        earliest_free = min(pool.get(station_id, [0.0]))
        charge_start = max(arrival, earliest_free)
        total_wait += charge_start - arrival
        current_t = charge_start + charge_time
        current_pos = positions[station_id]

    max_depth = max((max(pool[s]) for s in plan if s in pool), default=0.0)
    return total_wait, max_depth


def _book_plan(
    bus: Bus,
    plan: List[str],
    pool: Dict[str, List[float]],
    speed: float,
    charge_time: int,
    scenario: Scenario,
) -> None:
    """Book the plan into the charger pool in-place."""
    positions, origin_pos = _get_positions_and_origin(bus, scenario)
    current_t = time_str_to_minutes(bus.departure)
    current_pos = origin_pos

    for station_id in plan:
        travel_t = ((positions[station_id] - current_pos) / speed) * 60.0
        arrival = current_t + travel_t
        free_times = pool[station_id]
        charger_index = min(range(len(free_times)), key=lambda i: free_times[i])
        charge_start = max(arrival, free_times[charger_index])
        free_times[charger_index] = charge_start + charge_time
        current_t = charge_start + charge_time
        current_pos = positions[station_id]


def assign_charging_plans(scenario: Scenario) -> Dict[str, List[str]]:
    """Assign plans, allowing one extra stop when minimum-stop waits are excessive."""
    if not scenario.buses:
        return {}

    speed = scenario.physics.travel_speed_kmh
    charge_time = scenario.physics.charge_time_minutes

    valid_candidates: Dict[str, List[List[str]]] = {}
    minimum_stop_counts: Dict[str, int] = {}
    minimum_candidates: Dict[str, List[List[str]]] = {}
    for bus in scenario.buses:
        valid = get_valid_charging_plans(bus, scenario)
        if not valid:
            raise ValueError(
                f"No valid charging plan for bus '{bus.id}' from "
                f"{bus.origin_node} to {bus.destination_node}."
            )
        min_stops = min(len(plan) for plan in valid)
        valid_candidates[bus.id] = valid
        minimum_stop_counts[bus.id] = min_stops
        minimum_candidates[bus.id] = [
            plan for plan in valid if len(plan) == min_stops
        ]

    station_charger_frees: Dict[str, List[float]] = {
        station.id: [0.0] * max(station.active_charger_count, 1)
        for station in scenario.stations
    }

    def eligible_candidates(
        bus: Bus,
        pool: Dict[str, List[float]],
    ) -> List[List[str]]:
        candidates = minimum_candidates[bus.id]
        min_wait = min(
            _score_plan(bus, plan, pool, speed, charge_time, scenario)[0]
            for plan in candidates
        )
        if min_wait <= scenario.planner.extra_stop_wait_threshold_minutes:
            return candidates
        extra_stop_count = minimum_stop_counts[bus.id] + 1
        extra_candidates = [
            plan
            for plan in valid_candidates[bus.id]
            if len(plan) == extra_stop_count
        ]
        return candidates + extra_candidates if extra_candidates else candidates

    def plan_cost(
        bus: Bus,
        plan: List[str],
        pool: Dict[str, List[float]],
    ) -> Tuple[float, float, float]:
        wait, load = _score_plan(bus, plan, pool, speed, charge_time, scenario)
        extra_stops = max(0, len(plan) - minimum_stop_counts[bus.id])
        return wait + (extra_stops * charge_time), wait, load

    def predicted_first_arrival(bus: Bus) -> Tuple[float, float, str]:
        candidates = minimum_candidates[bus.id]
        non_empty = [plan for plan in candidates if plan]
        departure = time_str_to_minutes(bus.departure)
        if not non_empty:
            return float("inf"), departure, bus.id
        positions, origin_pos = _get_positions_and_origin(bus, scenario)
        earliest_travel = min(
            ((positions[plan[0]] - origin_pos) / speed) * 60.0
            for plan in non_empty
        )
        return departure + earliest_travel, departure, bus.id

    ordered_buses = sorted(scenario.buses, key=predicted_first_arrival)
    assignments: Dict[str, List[str]] = {}

    for index, bus in enumerate(ordered_buses):
        candidates = eligible_candidates(bus, station_charger_frees)
        next_bus = ordered_buses[index + 1] if index + 1 < len(ordered_buses) else None

        best_plan = candidates[0]
        best_score = (float("inf"), float("inf"), float("inf"))

        for plan in candidates:
            own_cost, _own_wait, load = plan_cost(bus, plan, station_charger_frees)
            if next_bus:
                trial_pool = {
                    station_id: list(free_times)
                    for station_id, free_times in station_charger_frees.items()
                }
                _book_plan(bus, plan, trial_pool, speed, charge_time, scenario)
                next_candidates = eligible_candidates(next_bus, trial_pool)
                next_best_cost = min(
                    plan_cost(next_bus, next_plan, trial_pool)[0]
                    for next_plan in next_candidates
                )
            else:
                next_best_cost = 0.0

            # Lookahead is a tiebreaker, not a joint objective: a bus should not
            # accept avoidable personal wait solely to improve the next bus.
            score = (own_cost, next_best_cost, load)
            if score < best_score:
                best_score = score
                best_plan = plan

        assignments[bus.id] = best_plan
        _book_plan(bus, best_plan, station_charger_frees, speed, charge_time, scenario)

    return assignments
