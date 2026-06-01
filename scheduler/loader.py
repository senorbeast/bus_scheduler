"""YAML loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scheduler.models import (
    Bus,
    Charger,
    ChargerState,
    DriverShift,
    OperatorConfig,
    Physics,
    PlannerConfig,
    RouteSegment,
    Scenario,
    Station,
    Weights,
    time_str_to_minutes,
)
from scheduler.routes.linear import LinearRouteProvider


def load_scenario(path: str, world_dir: str = "world") -> Scenario:
    """Load one scenario YAML and its referenced world YAML."""
    scenario_path = Path(path)
    with scenario_path.open("r", encoding="utf-8") as handle:
        scenario_data: dict[str, Any] = yaml.safe_load(handle) or {}
    _validate_scenario_shape(scenario_data)

    world_id = scenario_data["meta"]["world_id"]
    world_path = Path(world_dir) / f"{world_id}.yaml"
    with world_path.open("r", encoding="utf-8") as handle:
        world_data: dict[str, Any] = yaml.safe_load(handle) or {}
    _validate_world_shape(world_data)
    return _assemble_scenario(scenario_data, world_data)


def list_scenarios(folder: str = "scenarios") -> list[Path]:
    """Return sorted scenario YAML files."""
    return sorted(Path(folder).glob("scenario_*.yaml"))


def _validate_scenario_shape(data: dict[str, Any]) -> None:
    for key in ("meta", "operators", "weights", "buses"):
        if key not in data:
            raise ValueError(f"Scenario YAML missing required key: '{key}'")
    if "world_id" not in data["meta"]:
        raise ValueError("Scenario meta must include world_id.")


def _validate_world_shape(data: dict[str, Any]) -> None:
    for key in ("id", "route", "physics", "stations"):
        if key not in data:
            raise ValueError(f"World YAML missing required key: '{key}'")
    if "segments" not in data["route"]:
        raise ValueError("World route must include segments.")


def _assemble_scenario(
    scenario_data: dict[str, Any], world_data: dict[str, Any]
) -> Scenario:
    segments = [
        RouteSegment(
            from_node=segment["from"],
            to_node=segment["to"],
            distance_km=float(segment["distance_km"]),
        )
        for segment in world_data["route"]["segments"]
    ]
    station_ids = [station["id"] for station in world_data["stations"]]
    route_type = world_data["route"].get("type", "linear")
    if route_type != "linear":
        raise NotImplementedError(f"Route type '{route_type}' is not implemented in v1.")
    route = LinearRouteProvider(segments, station_ids)

    physics_data = world_data["physics"]
    physics = Physics(
        battery_range_km=float(physics_data["battery_range_km"]),
        charge_time_minutes=int(physics_data["charge_time_minutes"]),
        travel_speed_kmh=float(physics_data["travel_speed_kmh"]),
    )

    planner_data = world_data.get("planner", {})
    planner = PlannerConfig(
        extra_stop_wait_threshold_minutes=float(
            planner_data.get("extra_stop_wait_threshold_minutes", 120.0)
        ),
    )

    stations = [
        Station(
            id=station["id"],
            chargers=tuple(
                Charger(
                    id=charger["id"],
                    operational=bool(charger.get("operational", True)),
                    available_from=charger.get("available_from", "00:00"),
                    available_until=charger.get("available_until", "23:59"),
                )
                for charger in station["chargers"]
            ),
        )
        for station in world_data["stations"]
    ]

    operators = [
        OperatorConfig(
            id=operator["id"],
            weight=float(operator.get("weight", 1.0)),
        )
        for operator in scenario_data["operators"]
    ]

    weight_data = scenario_data.get("weights", {})
    weights = Weights(
        individual=float(weight_data.get("individual", 1.0)),
        operator=float(weight_data.get("operator", 1.0)),
        overall=float(weight_data.get("overall", 1.0)),
        shift=float(weight_data.get("shift", 0.0)),
    )

    buses = [_parse_bus(bus_data, route, physics) for bus_data in scenario_data["buses"]]
    _validate_operator_references(buses, operators)

    return Scenario(
        meta={"id": world_data["id"], **dict(scenario_data["meta"])},
        route=route,
        physics=physics,
        planner=planner,
        stations=stations,
        operators=operators,
        weights=weights,
        buses=buses,
    )


def _parse_bus(bus_data: dict[str, Any], route: LinearRouteProvider, physics: Physics) -> Bus:
    origin_node = bus_data.get("origin_node")
    destination_node = bus_data.get("destination_node")
    direction = bus_data.get("direction")
    if origin_node is None or destination_node is None:
        if direction == "BK":
            origin_node = route.origin
            destination_node = route.destination
        elif direction == "KB":
            origin_node = route.destination
            destination_node = route.origin
        else:
            raise ValueError(
                f"Bus '{bus_data.get('id', '?')}' must include origin_node/destination_node "
                "or a valid direction."
            )
    if not route.has_node(origin_node) or not route.has_node(destination_node):
        raise ValueError(f"Bus '{bus_data.get('id', '?')}' references an unknown route node.")
    derived_direction = route.get_direction(origin_node, destination_node)
    if direction is not None and direction != derived_direction:
        raise ValueError(
            f"Bus '{bus_data.get('id', '?')}' direction conflicts with origin/destination."
        )

    shift_data = bus_data.get("driver_shift")
    shift = DriverShift(shift_data["start"], shift_data["end"]) if shift_data else None
    initial_range = bus_data.get("initial_range_km")
    return Bus(
        id=bus_data["id"],
        operator=bus_data["operator"],
        departure=bus_data["departure"],
        origin_node=origin_node,
        destination_node=destination_node,
        weight=float(bus_data.get("weight", 1.0)),
        requires_origin_charge=bool(bus_data.get("requires_origin_charge", False)),
        initial_range_km=float(initial_range) if initial_range is not None else physics.battery_range_km,
        direction=derived_direction,
        driver_shift=shift,
    )


def _validate_operator_references(buses: list[Bus], operators: list[OperatorConfig]) -> None:
    operator_ids = {operator.id for operator in operators}
    for bus in buses:
        if bus.operator not in operator_ids:
            raise ValueError(f"Bus '{bus.id}' references unknown operator '{bus.operator}'.")


def build_charger_state(charger: Charger) -> ChargerState:
    """Convert static charger config into mutable runtime state."""
    return ChargerState(
        charger_id=charger.id,
        available_from=time_str_to_minutes(charger.available_from),
        available_until=time_str_to_minutes(charger.available_until),
        is_operational=charger.operational,
        free_at=0.0,
    )
