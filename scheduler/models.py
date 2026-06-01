"""Typed data models and shared utilities for the bus scheduler."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Final

VALID_DIRECTIONS: Final[set[str]] = {"BK", "KB"}
MINUTES_PER_DAY: Final[int] = 1440


def time_str_to_minutes(t: str) -> float:
    """Convert HH:MM to minutes from midnight."""
    hour, minute = t.split(":")
    return float(int(hour) * 60 + int(minute))


def minutes_to_time_str(m: float) -> str:
    """Convert minutes from midnight to HH:MM, prefixing +Nd for later days."""
    total = int(round(m))
    day_offset = total // MINUTES_PER_DAY
    remainder = total % MINUTES_PER_DAY
    hour = remainder // 60
    minute = remainder % 60
    prefix = f"+{day_offset}d " if day_offset > 0 else ""
    return f"{prefix}{hour:02d}:{minute:02d}"


def driver_shift_end_minutes(shift: DriverShift) -> float:
    """Return absolute shift end, handling shifts that cross midnight."""
    start = time_str_to_minutes(shift.start)
    end = time_str_to_minutes(shift.end)
    if end < start:
        end += MINUTES_PER_DAY
    return end


class RouteProvider(ABC):
    """Abstract interface for route topology."""

    @property
    @abstractmethod
    def origin(self) -> str:
        """Route origin terminal."""

    @property
    @abstractmethod
    def destination(self) -> str:
        """Route destination terminal."""

    @abstractmethod
    def get_node_positions(self, direction: str) -> dict[str, float]:
        """Return node positions measured from the direction origin."""

    @abstractmethod
    def get_station_ids(self) -> list[str]:
        """Return charging station IDs, excluding terminals."""

    @abstractmethod
    def get_total_distance(self) -> float:
        """Return full route distance."""

    @abstractmethod
    def get_next_reachable_stations(
        self, from_node: str, direction: str, range_km: float
    ) -> list[str]:
        """Return reachable charging stations ahead of from_node."""

    @abstractmethod
    def get_direction(self, origin_node: str, destination_node: str) -> str:
        """Return BK or KB for a pair of nodes."""

    @abstractmethod
    def get_distance_between(
        self, from_node: str, to_node: str, direction: str | None = None
    ) -> float:
        """Return forward route distance between two nodes."""

    @abstractmethod
    def get_station_ids_between(self, origin_node: str, destination_node: str) -> list[str]:
        """Return charging stations strictly between two endpoints."""

    @abstractmethod
    def has_node(self, node_id: str) -> bool:
        """Return whether the route contains node_id."""


@dataclass(frozen=True)
class RoutePositions:
    """Position lookup plus total route/trip distance."""

    positions: dict[str, float]
    total_distance: float


@dataclass(frozen=True)
class RouteSegment:
    """One road segment in the linear corridor."""

    from_node: str
    to_node: str
    distance_km: float


@dataclass(frozen=True)
class Physics:
    """World-level physics constants."""

    battery_range_km: float
    charge_time_minutes: int
    travel_speed_kmh: float


@dataclass(frozen=True)
class PlannerConfig:
    """Planner policy loaded from world configuration."""

    extra_stop_wait_threshold_minutes: float = 120.0


@dataclass(frozen=True)
class Charger:
    """Physical charger configuration."""

    id: str
    operational: bool
    available_from: str
    available_until: str


@dataclass(frozen=True)
class Station:
    """Charging station configuration."""

    id: str
    chargers: tuple[Charger, ...]

    @property
    def active_charger_count(self) -> int:
        return sum(1 for charger in self.chargers if charger.operational)


@dataclass(frozen=True)
class DriverShift:
    """Driver shift window."""

    start: str
    end: str


@dataclass(frozen=True)
class Bus:
    """One bus trip through part or all of the corridor."""

    id: str
    operator: str
    departure: str
    origin_node: str
    destination_node: str
    weight: float = 1.0
    requires_origin_charge: bool = False
    initial_range_km: float | None = None
    direction: str | None = None
    driver_shift: DriverShift | None = None


@dataclass(frozen=True)
class OperatorConfig:
    """Operator-level scheduling configuration."""

    id: str
    weight: float = 1.0


@dataclass(frozen=True)
class Weights:
    """Soft-rule weights loaded from scenario YAML."""

    individual: float = 1.0
    operator: float = 1.0
    overall: float = 1.0
    shift: float = 0.0


@dataclass(frozen=True)
class Scenario:
    """Runtime scenario assembled from world and scenario YAML."""

    meta: dict[str, str]
    route: RouteProvider
    physics: Physics
    planner: PlannerConfig
    stations: list[Station]
    operators: list[OperatorConfig]
    weights: Weights
    buses: list[Bus]

    @property
    def origin(self) -> str:
        return self.route.origin

    @property
    def destination(self) -> str:
        return self.route.destination

    @property
    def station_ids(self) -> list[str]:
        return self.route.get_station_ids()

    def get_operator(self, operator_id: str) -> OperatorConfig | None:
        return next((op for op in self.operators if op.id == operator_id), None)

    def get_station(self, station_id: str) -> Station | None:
        return next((station for station in self.stations if station.id == station_id), None)


@dataclass
class ChargingEvent:
    """Record of one completed charging session."""

    station_id: str
    arrival_time: float
    wait_time: float
    charge_start: float
    charge_end: float
    charger_id: str
    event_kind: str = "en_route"


@dataclass
class BusState:
    """Mutable live state of a bus during simulation."""

    bus: Bus
    charging_plan: list[str]
    current_range_km: float
    current_time: float
    position: str
    direction: str
    station_arrival_time: float = 0.0
    current_plan_index: int = 0
    completed_events: list[ChargingEvent] = field(default_factory=list)
    total_wait_time: float = 0.0
    route_departure_time: float | None = None
    done: bool = False


@dataclass
class ChargerState:
    """Mutable runtime state of one charger."""

    charger_id: str
    available_from: float = 0.0
    available_until: float = float(MINUTES_PER_DAY)
    is_operational: bool = True
    free_at: float = 0.0

    def can_charge_at(self, t: float) -> bool:
        day_t = t % MINUTES_PER_DAY
        return (
            self.is_operational
            and self.available_from <= day_t <= self.available_until
            and self.free_at <= t
        )

    def next_available_time(self, t: float) -> float | None:
        """Return the next time this charger can start a session, or None if failed."""
        if not self.is_operational:
            return None
        candidate = max(t, self.free_at)
        day_start = candidate - (candidate % MINUTES_PER_DAY)
        day_t = candidate % MINUTES_PER_DAY
        if self.available_from <= day_t <= self.available_until:
            return candidate
        if day_t < self.available_from:
            return day_start + self.available_from
        return day_start + MINUTES_PER_DAY + self.available_from


@dataclass
class StationState:
    """Mutable state for station queue, charger pool, and logs."""

    station_id: str
    charger_states: list[ChargerState]
    waiting_queue: list[str] = field(default_factory=list)
    charge_log: list[dict[str, Any]] = field(default_factory=list)

    def get_free_charger_at(self, t: float) -> ChargerState | None:
        available = [charger for charger in self.charger_states if charger.can_charge_at(t)]
        return min(available, key=lambda charger: charger.free_at) if available else None

    def get_earliest_free_charger(self) -> ChargerState:
        return min(self.charger_states, key=lambda charger: charger.free_at)

    def get_charger(self, charger_id: str) -> ChargerState:
        for charger in self.charger_states:
            if charger.charger_id == charger_id:
                return charger
        raise ValueError(f"Unknown charger '{charger_id}' at station '{self.station_id}'.")

    def get_next_available_time(self, t: float) -> float | None:
        candidates = [
            next_time
            for charger in self.charger_states
            if (next_time := charger.next_available_time(t)) is not None
        ]
        return min(candidates) if candidates else None

    def has_operational_charger(self) -> bool:
        return any(charger.is_operational for charger in self.charger_states)


@dataclass
class ScheduleContext:
    """Shared simulation context passed to soft rules."""

    scenario: Scenario
    bus_states: dict[str, BusState]
    station_states: dict[str, StationState]
    current_time: float

    @property
    def time_of_day(self) -> float:
        return self.current_time % MINUTES_PER_DAY

    def get_operator_delays(self, operator_id: str) -> list[float]:
        return [
            state.total_wait_time
            for state in self.bus_states.values()
            if state.bus.operator == operator_id and state.completed_events
        ]

    def get_remaining_distance(self, bus_state: BusState) -> float:
        return self.scenario.route.get_distance_between(
            bus_state.position, bus_state.bus.destination_node, bus_state.direction
        )


@dataclass(frozen=True)
class BusTimetable:
    """Per-bus simulation output."""

    bus_id: str
    operator: str
    direction: str
    origin_node: str
    destination_node: str
    departure_time: float
    route_departure_time: float
    charging_plan: list[str]
    charging_events: list[ChargingEvent]
    total_wait_time: float
    arrival_time: float
    total_trip_time: float


@dataclass(frozen=True)
class StationChargeLog:
    """Per-station charge log output."""

    station_id: str
    entries: list[dict[str, Any]]


@dataclass(frozen=True)
class SimulationResult:
    """Complete simulation output."""

    scenario_id: str
    bus_timetables: list[BusTimetable]
    station_logs: list[StationChargeLog]
    total_network_wait_minutes: float
    per_operator_avg_wait: dict[str, float]
    simulation_duration_minutes: float
    avg_trip_time_minutes: float
    max_single_bus_wait_minutes: float
