"""Discrete-event simulation engine."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import Enum, auto

from scheduler.loader import build_charger_state
from scheduler.models import (
    Bus,
    BusState,
    BusTimetable,
    ChargerState,
    ChargingEvent,
    Scenario,
    ScheduleContext,
    SimulationResult,
    StationChargeLog,
    StationState,
    time_str_to_minutes,
)
from scheduler.planner import assign_charging_plans
from scheduler.rules.soft_rules import (
    DriverShiftProximityRule,
    IndividualWaitRule,
    OperatorFairnessRule,
    OverallThroughputRule,
)
from scheduler.scoring import WeightedScorer


class EventType(Enum):
    """Simulation event types."""

    BUS_READY_TO_DEPART = auto()
    BUS_ARRIVES_AT_STATION = auto()
    CHARGING_COMPLETE = auto()
    QUEUE_RECHECK = auto()


@dataclass(order=True)
class Event:
    """One deterministic heap event."""

    time: float
    sequence: int
    event_type: EventType = field(compare=False)
    bus_id: str = field(compare=False)
    station_id: str = field(compare=False)
    event_kind: str = field(default="en_route", compare=False)
    charger_id: str | None = field(default=None, compare=False)


def run_simulation(scenario: Scenario) -> SimulationResult:
    """Run the discrete-event simulation for a loaded scenario."""
    scorer = WeightedScorer(
        weights=scenario.weights,
        rules=[
            (IndividualWaitRule(), "individual"),
            (OperatorFairnessRule(), "operator"),
            (OverallThroughputRule(), "overall"),
            (DriverShiftProximityRule(), "shift"),
        ],
    )
    bus_plans = assign_charging_plans(scenario)
    bus_states = _build_bus_states(scenario, bus_plans)
    station_states = {
        station.id: StationState(
            station_id=station.id,
            charger_states=[
                build_charger_state(charger)
                for charger in station.chargers
                if charger.operational
            ],
        )
        for station in scenario.stations
    }
    context = ScheduleContext(scenario, bus_states, station_states, current_time=0.0)
    event_queue: list[Event] = []
    seq = 0
    for bus in scenario.buses:
        state = bus_states[bus.id]
        departure = time_str_to_minutes(bus.departure)
        if bus.requires_origin_charge:
            if bus.origin_node not in station_states:
                raise ValueError(
                    f"Bus '{bus.id}' requires origin charging, but "
                    f"'{bus.origin_node}' has no charger."
                )
            heapq.heappush(
                event_queue,
                Event(
                    departure,
                    seq,
                    EventType.BUS_ARRIVES_AT_STATION,
                    bus.id,
                    bus.origin_node,
                    "origin",
                ),
            )
        else:
            heapq.heappush(
                event_queue,
                Event(
                    departure,
                    seq,
                    EventType.BUS_READY_TO_DEPART,
                    bus.id,
                    bus.origin_node,
                ),
            )
        seq += 1

    while event_queue:
        event = heapq.heappop(event_queue)
        context.current_time = event.time
        if event.event_type == EventType.BUS_READY_TO_DEPART:
            seq = _handle_ready_to_depart(event, context, event_queue, seq)
        elif event.event_type == EventType.BUS_ARRIVES_AT_STATION:
            seq = _handle_arrival(event, context, scorer, event_queue, seq)
        elif event.event_type == EventType.CHARGING_COMPLETE:
            seq = _handle_charge_complete(event, context, scorer, event_queue, seq)
        elif event.event_type == EventType.QUEUE_RECHECK:
            seq = _handle_queue_recheck(event, context, scorer, event_queue, seq)

    return _build_result(scenario, bus_states, station_states)


def _build_bus_states(
    scenario: Scenario, bus_plans: dict[str, list[str]]
) -> dict[str, BusState]:
    states: dict[str, BusState] = {}
    for bus in scenario.buses:
        direction = bus.direction or scenario.route.get_direction(
            bus.origin_node, bus.destination_node
        )
        initial_range = (
            scenario.physics.battery_range_km
            if bus.initial_range_km is None
            else bus.initial_range_km
        )
        departure = time_str_to_minutes(bus.departure)
        states[bus.id] = BusState(
            bus=bus,
            charging_plan=bus_plans[bus.id],
            current_range_km=initial_range,
            current_time=departure,
            position=bus.origin_node,
            direction=direction,
            route_departure_time=departure if not bus.requires_origin_charge else None,
        )
    return states


def _handle_ready_to_depart(
    event: Event,
    context: ScheduleContext,
    event_queue: list[Event],
    seq: int,
) -> int:
    state = context.bus_states[event.bus_id]
    state.current_time = event.time
    state.position = state.bus.origin_node
    state.route_departure_time = event.time
    return _schedule_next_travel(state, context, event_queue, seq, event.time)


def _handle_arrival(
    event: Event,
    context: ScheduleContext,
    scorer: WeightedScorer,
    event_queue: list[Event],
    seq: int,
) -> int:
    state = context.bus_states[event.bus_id]
    station_state = context.station_states[event.station_id]
    state.position = event.station_id
    state.current_time = event.time
    state.station_arrival_time = event.time
    free = station_state.get_free_charger_at(event.time)
    if free is not None:
        return _start_charging(
            event.bus_id, event.station_id, event.time, free, context, event_queue, seq, event.event_kind
        )
    station_state.waiting_queue.append(event.bus_id)
    return _schedule_queue_recheck_if_needed(station_state, event.time, event_queue, seq)


def _handle_charge_complete(
    event: Event,
    context: ScheduleContext,
    scorer: WeightedScorer,
    event_queue: list[Event],
    seq: int,
) -> int:
    state = context.bus_states[event.bus_id]
    station_state = context.station_states[event.station_id]
    state.current_time = event.time
    state.current_range_km = context.scenario.physics.battery_range_km
    if event.event_kind == "origin":
        state.route_departure_time = event.time
    else:
        state.current_plan_index += 1

    if event.charger_id is None:
        raise RuntimeError("CHARGING_COMPLETE event missing charger_id.")
    freed = station_state.get_charger(event.charger_id)
    if station_state.waiting_queue and freed.can_charge_at(event.time):
        waiting = [context.bus_states[bus_id] for bus_id in station_state.waiting_queue]
        scored = sorted(
            ((scorer.score(waiting_state, context), waiting_state.bus.id) for waiting_state in waiting),
            reverse=True,
        )
        next_bus_id = scored[0][1]
        station_state.waiting_queue.remove(next_bus_id)
        next_kind = "origin" if context.bus_states[next_bus_id].position == context.bus_states[next_bus_id].bus.origin_node and context.bus_states[next_bus_id].route_departure_time is None else "en_route"
        seq = _start_charging(
            next_bus_id, event.station_id, event.time, freed, context, event_queue, seq, next_kind
        )
    elif station_state.waiting_queue:
        seq = _schedule_queue_recheck_if_needed(station_state, event.time, event_queue, seq)

    return _schedule_next_travel(state, context, event_queue, seq, event.time)


def _handle_queue_recheck(
    event: Event,
    context: ScheduleContext,
    scorer: WeightedScorer,
    event_queue: list[Event],
    seq: int,
) -> int:
    station_state = context.station_states[event.station_id]
    if not station_state.waiting_queue:
        return seq
    free = station_state.get_free_charger_at(event.time)
    if free is None:
        return _schedule_queue_recheck_if_needed(station_state, event.time, event_queue, seq)
    waiting = [context.bus_states[bus_id] for bus_id in station_state.waiting_queue]
    scored = sorted(
        ((scorer.score(waiting_state, context), waiting_state.bus.id) for waiting_state in waiting),
        reverse=True,
    )
    next_bus_id = scored[0][1]
    station_state.waiting_queue.remove(next_bus_id)
    next_state = context.bus_states[next_bus_id]
    event_kind = (
        "origin"
        if next_state.position == next_state.bus.origin_node
        and next_state.route_departure_time is None
        else "en_route"
    )
    return _start_charging(
        next_bus_id, event.station_id, event.time, free, context, event_queue, seq, event_kind
    )


def _start_charging(
    bus_id: str,
    station_id: str,
    start_time: float,
    charger: ChargerState,
    context: ScheduleContext,
    event_queue: list[Event],
    seq: int,
    event_kind: str,
) -> int:
    state = context.bus_states[bus_id]
    station_state = context.station_states[station_id]
    charge_end = start_time + context.scenario.physics.charge_time_minutes
    wait_time = start_time - state.station_arrival_time
    charge_event = ChargingEvent(
        station_id=station_id,
        arrival_time=state.station_arrival_time,
        wait_time=wait_time,
        charge_start=start_time,
        charge_end=charge_end,
        charger_id=charger.charger_id,
        event_kind=event_kind,
    )
    state.completed_events.append(charge_event)
    state.total_wait_time += wait_time
    charger.free_at = charge_end
    station_state.charge_log.append(
        {
            "bus_id": bus_id,
            "operator": state.bus.operator,
            "arrival_time": state.station_arrival_time,
            "wait_time": wait_time,
            "charge_start": start_time,
            "charge_end": charge_end,
            "charger_id": charger.charger_id,
            "event_kind": event_kind,
        }
    )
    heapq.heappush(
        event_queue,
        Event(
            charge_end,
            seq,
            EventType.CHARGING_COMPLETE,
            bus_id,
            station_id,
            event_kind,
            charger.charger_id,
        ),
    )
    return seq + 1


def _schedule_queue_recheck_if_needed(
    station_state: StationState,
    now: float,
    event_queue: list[Event],
    seq: int,
) -> int:
    if any(charger.is_operational and charger.free_at > now for charger in station_state.charger_states):
        return seq
    next_time = station_state.get_next_available_time(now)
    if next_time is None or next_time <= now:
        return seq
    heapq.heappush(
        event_queue,
        Event(next_time, seq, EventType.QUEUE_RECHECK, "", station_state.station_id),
    )
    return seq + 1


def _schedule_next_travel(
    state: BusState,
    context: ScheduleContext,
    event_queue: list[Event],
    seq: int,
    start_time: float,
) -> int:
    target = _next_target(state)
    if target is None:
        state.done = True
        return seq
    distance = context.scenario.route.get_distance_between(
        state.position, target, state.direction
    )
    if distance > state.current_range_km + 1e-9:
        raise RuntimeError(
            f"Bus '{state.bus.id}' cannot travel {distance:.1f}km from "
            f"{state.position} to {target} with {state.current_range_km:.1f}km range."
        )
    state.current_range_km -= distance
    arrival = start_time + (distance / context.scenario.physics.travel_speed_kmh) * 60.0
    if target == state.bus.destination_node:
        state.current_time = arrival
        state.position = target
        state.done = True
        return seq
    heapq.heappush(
        event_queue,
        Event(arrival, seq, EventType.BUS_ARRIVES_AT_STATION, state.bus.id, target, "en_route"),
    )
    return seq + 1


def _next_target(state: BusState) -> str | None:
    if state.current_plan_index < len(state.charging_plan):
        return state.charging_plan[state.current_plan_index]
    if state.position != state.bus.destination_node:
        return state.bus.destination_node
    return None


def _build_result(
    scenario: Scenario,
    bus_states: dict[str, BusState],
    station_states: dict[str, StationState],
) -> SimulationResult:
    timetables = _build_timetables(bus_states)
    logs = _build_station_logs(station_states)
    waits = [timetable.total_wait_time for timetable in timetables]
    per_operator: dict[str, float] = {}
    for operator in {timetable.operator for timetable in timetables}:
        op_waits = [t.total_wait_time for t in timetables if t.operator == operator]
        per_operator[operator] = sum(op_waits) / len(op_waits) if op_waits else 0.0
    departures = [timetable.departure_time for timetable in timetables]
    arrivals = [timetable.arrival_time for timetable in timetables]
    return SimulationResult(
        scenario_id=scenario.meta.get("id", "unknown"),
        bus_timetables=timetables,
        station_logs=logs,
        total_network_wait_minutes=sum(waits),
        per_operator_avg_wait=per_operator,
        simulation_duration_minutes=max(arrivals) - min(departures) if arrivals else 0.0,
        max_single_bus_wait_minutes=max(waits) if waits else 0.0,
    )


def _build_timetables(bus_states: dict[str, BusState]) -> list[BusTimetable]:
    result = []
    for state in bus_states.values():
        departure = time_str_to_minutes(state.bus.departure)
        route_departure = state.route_departure_time or departure
        result.append(
            BusTimetable(
                bus_id=state.bus.id,
                operator=state.bus.operator,
                direction=state.direction,
                origin_node=state.bus.origin_node,
                destination_node=state.bus.destination_node,
                departure_time=departure,
                route_departure_time=route_departure,
                charging_plan=state.charging_plan,
                charging_events=list(state.completed_events),
                total_wait_time=state.total_wait_time,
                arrival_time=state.current_time,
                total_trip_time=state.current_time - departure,
            )
        )
    return sorted(result, key=lambda timetable: (timetable.departure_time, timetable.bus_id))


def _build_station_logs(station_states: dict[str, StationState]) -> list[StationChargeLog]:
    return [
        StationChargeLog(
            station_id=station_id,
            entries=sorted(station_state.charge_log, key=lambda entry: entry["charge_start"]),
        )
        for station_id, station_state in sorted(station_states.items())
    ]
