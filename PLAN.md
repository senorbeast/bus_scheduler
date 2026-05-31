# PLAN.md — Implementation Blueprint (v2.1)

> All issues from REFUTE.md that can be fixed without core changes are resolved here.
> R-16, R-17, R-24 require moderate engine changes and are flagged where relevant.
> Cross-references: [R-XX] = REFUTE.md issue. [FC-XX] = FUTURE_CHANGES.md scenario.

---

## 1. Project Directory Structure

```
bus_scheduler/
├── app.py                         # Streamlit entry point
├── requirements.txt
├── README.md
├── ARCHITECTURE.md
├── ASSUMPTIONS.md
├── FUTURE_CHANGES.md
├── OVERVIEW.md
├── PLAN.md
├── REFUTE.md
│
├── world/                         # Physical infrastructure (rarely changes) [R-18 fix]
│   └── bengaluru_kochi.yaml       # Route topology, station hardware, physics
│
├── scenarios/                     # Run-specific config (changes per test)
│   ├── scenario_1.yaml
│   ├── scenario_2.yaml
│   ├── scenario_3.yaml
│   ├── scenario_4.yaml
│   └── scenario_5.yaml
│
├── scheduler/
│   ├── __init__.py
│   ├── models.py           # ALL dataclasses + RouteProvider ABC + SimulationResult [R-17, R-21 fix]
│   ├── loader.py           # YAML → Scenario (handles world/scenario split) [R-14, R-18 fix]
│   ├── planner.py          # Valid plan generator (uses RouteProvider) [R-15 fix]
│   ├── engine.py           # Discrete-event simulation
│   ├── scoring.py          # WeightedScorer
│   │
│   ├── routes/             # Route topology implementations [R-17 fix]
│   │   ├── __init__.py
│   │   └── linear.py       # LinearRouteProvider — current single-path implementation
│   │                       # graph.py goes here for V2 NetworkX implementation
│   │
│   └── rules/
│       ├── __init__.py
│       ├── base.py         # SoftRule, HardRule abstract classes
│       ├── hard_rules.py   # RangeConstraint, StationOrderConstraint
│       └── soft_rules.py   # IndividualWaitRule, OperatorFairnessRule, OverallThroughputRule,
│                           # DriverShiftProximityRule
│
└── ui/
    ├── __init__.py
    ├── scenario_view.py
    ├── bus_timetable.py
    └── station_view.py
```

---

## 2. YAML Schema

### `world/bengaluru_kochi.yaml` — Physical Infrastructure [R-18 fix]

```yaml
# World file: physical infrastructure that rarely changes.
# Owned by the infra/ops team. Referenced by scenario files via world_id.
# Changing charger count, battery physics, or route segments happens here only.

id: "bengaluru_kochi"
name: "Bengaluru–Kochi Corridor"

route:
  type: "linear"        # "linear" or "graph" — loader selects RouteProvider impl
  segments:
    - {from: "Bengaluru", to: "A",     distance_km: 100}
    - {from: "A",         to: "B",     distance_km: 120}
    - {from: "B",         to: "C",     distance_km: 100}
    - {from: "C",         to: "D",     distance_km: 120}
    - {from: "D",         to: "Kochi", distance_km: 100}

physics:
  battery_range_km: 240
  charge_time_minutes: 25
  travel_speed_kmh: 60
  charge_to_full: true

stations:
  - id: "A"
    chargers:
      - {id: "A-1", operational: true, available_from: "00:00", available_until: "23:59"}
  - id: "B"
    chargers:
      - {id: "B-1", operational: true, available_from: "00:00", available_until: "23:59"}
  - id: "C"
    chargers:
      - {id: "C-1", operational: true, available_from: "00:00", available_until: "23:59"}
  - id: "D"
    chargers:
      - {id: "D-1", operational: true, available_from: "00:00", available_until: "23:59"}
```

### `scenarios/scenario_1.yaml` — Run Configuration [R-18 fix]

```yaml
# Scenario file: what changes run-to-run (buses, weights, operator tuning).
# References a world file via world_id.
# The ops team edits this; never needs to touch the world file.

meta:
  id: "scenario-1"
  world_id: "bengaluru_kochi"   # ← references world/bengaluru_kochi.yaml
  name: "Even Spacing"
  description: "Buses depart every 15 minutes in each direction. Baseline case."
  version: "1.0"

operators:
  - {id: "kpn",      display_name: "KPN Travels", weight: 1.0}
  - {id: "freshbus", display_name: "FreshBus",    weight: 1.0}
  - {id: "flixbus",  display_name: "FlixBus",     weight: 1.0}

weights:
  individual: 1.0
  operator: 1.0
  overall: 1.0

buses:
  - {id: "bus-BK-01", operator: "kpn",      direction: "BK", departure: "19:00",
     priority_class: "standard", weight: 1.0}
  - {id: "bus-BK-02", operator: "freshbus", direction: "BK", departure: "19:15",
     priority_class: "standard", weight: 1.0}
  # ... (full list in scenario files)
  # Optional per-bus fields:
  # charge_strategy: "required"  # or "full" (default)
  # driver_shift:
  #   start: "18:00"
  #   end: "06:00"
```

---

## 3. Data Models — `scheduler/models.py`

```python
"""
scheduler/models.py
===================
Central data model for the bus charging scheduler.

Dependency rule: this file has ZERO imports from other scheduler modules.
All other modules import FROM here; nothing imports here reciprocally.

Sections:
  1. Constants and time utilities
  2. RouteProvider ABC              — pluggable topology interface [R-17 fix]
  3. Route and physics models
  4. Station models                 — includes ChargerState.can_charge_at() [R-25 fix]
  5. Bus and operator models
  6. Scenario model                 — uses RouteProvider, not raw segments
  7. Simulation state models        — BusState, StationState, ScheduleContext
  8. ScheduleContext                — [R-08, R-09, R-19, R-22 fixes]
  9. Output models                  — BusTimetable, StationChargeLog, SimulationResult [R-21 fix]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Final, List, NamedTuple, Optional, Set

# ── 1. CONSTANTS AND TIME UTILITIES ──────────────────────────────────────────

VALID_DIRECTIONS: Final[Set[str]] = {"BK", "KB"}
MINUTES_PER_DAY:  Final[int]      = 1440


def time_str_to_minutes(t: str) -> float:
    """
    Convert 'HH:MM' string to float minutes from midnight.

    Examples:
        '00:00' → 0.0
        '19:00' → 1140.0
        '23:59' → 1439.0
    """
    parts: List[str] = t.split(":")
    return float(int(parts[0]) * 60 + int(parts[1]))


def minutes_to_time_str(m: float) -> str:
    """
    Convert float minutes-from-midnight to human-readable string.

    Handles values > 1440 (next-day travel) with a '+Nd' prefix.  [R-01 fix]

    Examples:
        1140.0  → '19:00'
        1820.0  → '+1d 06:20'   (bus arrives next morning)
        2880.0  → '+2d 00:00'
    """
    total_m: int    = int(round(m))
    day_offset: int = total_m // MINUTES_PER_DAY
    remainder: int  = total_m % MINUTES_PER_DAY
    h: int          = remainder // 60
    mn: int         = remainder % 60
    prefix: str     = f"+{day_offset}d " if day_offset > 0 else ""
    return f"{prefix}{h:02d}:{mn:02d}"


def driver_shift_end_minutes(shift: DriverShift) -> float:
    """
    Return the absolute shift-end time in minutes-from-midnight.

    Handles shifts that cross midnight: if end < start, end is on the next day.  [R-13 fix]

    Examples:
        start='17:00', end='01:00' → 1500.0  (17*60 + (1*60 + 1440) via wrap)
        start='08:00', end='20:00' → 1200.0  (no wrap needed)
    """
    start: float = time_str_to_minutes(shift.start)
    end: float   = time_str_to_minutes(shift.end)
    if end < start:        # shift crosses midnight
        end += MINUTES_PER_DAY
    return end


# ── 2. ROUTEPROVIDER ABSTRACT BASE CLASS ──────────────────────────────────────

class RouteProvider(ABC):
    """
    Abstract interface for route topology.  [R-17 fix]

    Separates "how do I move through space" from "when do I charge".
    The scheduler (engine, planner, rules) interacts with routes ONLY through this
    interface. Swapping LinearRouteProvider for GraphRouteProvider is one change
    in the loader — zero changes in the engine, planner, or rules.

    Implementations:
        LinearRouteProvider  — single ordered segment list (current, V1)
        GraphRouteProvider   — NetworkX multi-path topology (V2 placeholder)

    All position data is pre-computed at construction. Every method is O(1) or O(stations).
    """

    @property
    @abstractmethod
    def origin(self) -> str:
        """Terminal origin node ID (e.g., 'Bengaluru')."""
        ...

    @property
    @abstractmethod
    def destination(self) -> str:
        """Terminal destination node ID (e.g., 'Kochi')."""
        ...

    @abstractmethod
    def get_node_positions(self, direction: str) -> Dict[str, float]:
        """
        Return {node_id: cumulative_distance_km_from_direction_origin} for all nodes.

        Direction-aware:
            'BK': distances measured from Bengaluru (Bengaluru=0, Kochi=540)
            'KB': distances measured from Kochi     (Kochi=0, Bengaluru=540)

        Used by engine to compute travel times and by rules to compute remaining distance.
        All values pre-computed at construction — this method is O(1).
        """
        ...

    @abstractmethod
    def get_station_ids(self) -> List[str]:
        """
        Return all intermediate charging station IDs (not origin or destination terminals).

        Order is not guaranteed — the planner sorts by position.
        Does NOT filter by operational status — that is the engine's job via StationState.
        """
        ...

    @abstractmethod
    def get_total_distance(self) -> float:
        """Total route length in km (origin → destination)."""
        ...

    @abstractmethod
    def get_next_reachable_stations(
        self,
        from_node: str,
        direction: str,
        range_km: float,
    ) -> List[str]:
        """
        Return all intermediate stations reachable from from_node within range_km,
        in the given direction (forward-only — no backtracking).

        Used for:
        - JIT charging plan evaluation (V2: dynamic re-routing after charger failure)
        - Plan validation in the planner

        Returns stations sorted by distance from from_node (nearest first).
        Returns empty list if no station is reachable within range.
        """
        ...


# ── 3. ROUTE AND PHYSICS MODELS ───────────────────────────────────────────────

@dataclass(frozen=True)
class RouteSegment:
    """
    One directed road segment between two nodes.
    Segments chain together to form the full route in the world YAML.
    """
    from_node:   str
    to_node:     str
    distance_km: float


@dataclass(frozen=True)
class Physics:
    """
    Physical constants for the route. Lives in the world YAML file.
    Global defaults — some fields may be overridden per-bus or per-charger (see FC-01, FC-02).
    """
    battery_range_km:     float           # max range on a full charge
    charge_time_minutes:  int             # time to charge to full (or to required level)
    travel_speed_kmh:     float           # assumed constant across all segments
    charge_to_full:       bool = True     # if False, charge only to required range (FC-03)


# ── 4. STATION MODELS ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Charger:
    """
    One physical charging unit at a station.
    Frozen because charger *hardware configuration* doesn't change mid-run.
    Runtime availability is tracked in ChargerState (mutable).
    """
    id:              str
    operational:     bool     # False = hardware failure; loader skips these for ChargerState
    available_from:  str      # 'HH:MM' — start of operational window
    available_until: str      # 'HH:MM' — end of operational window


@dataclass(frozen=True)
class Station:
    """
    One intermediate charging station on the route.
    Holds hardware config only. Runtime queue and charger state → StationState.
    """
    id:       str
    chargers: tuple            # tuple[Charger, ...] — tuple preserves frozen dataclass invariant

    @property
    def active_charger_count(self) -> int:
        """Count of chargers currently marked operational (not failed)."""
        return sum(1 for c in self.chargers if c.operational)


# ── 5. BUS AND OPERATOR MODELS ────────────────────────────────────────────────

@dataclass(frozen=True)
class DriverShift:
    """
    Driver's working hours for one trip. Optional — absent means no shift constraint.
    Use driver_shift_end_minutes() to compute absolute shift end (handles midnight crossing).
    """
    start: str    # 'HH:MM'
    end:   str    # 'HH:MM' — may be next-day; use driver_shift_end_minutes()


@dataclass(frozen=True)
class Bus:
    """
    One bus scheduled to make the trip.

    Fields:
        direction:       'BK' (Bengaluru→Kochi) or 'KB' (Kochi→Bengaluru).
        departure:       Scheduled departure time 'HH:MM'.
        priority_class:  String tag for rule-based priority bonuses ('standard', 'priority', 'vip').
        weight:          Multiplies all soft rule scores. Higher = charges sooner everywhere.
        charge_strategy: 'full' (default) or 'required' (top-up only — FC-23).
        driver_shift:    Optional shift hours. Governs DriverShiftProximityRule scoring.
    """
    id:              str
    operator:        str
    direction:       str                            # must be in VALID_DIRECTIONS
    departure:       str                            # 'HH:MM'
    priority_class:  str                  = "standard"
    weight:          float                = 1.0
    charge_strategy: str                  = "full"  # 'full' or 'required' [FC-23]
    driver_shift:    Optional[DriverShift] = None


@dataclass(frozen=True)
class OperatorConfig:
    """
    One bus operator on the network.
    weight multiplies the OperatorFairnessRule score for all this operator's buses.
    """
    id:           str
    display_name: str
    weight:       float = 1.0


@dataclass(frozen=True)
class Weights:
    """
    Global rule weight configuration. Stored in scenario YAML, not world YAML.
    All fields have defaults so adding new fields never breaks existing scenarios.

    Scale guidance (see REFUTE.md R-07):
        OverallThroughputRule natural range: [100, 540]
        IndividualWaitRule natural range:    [0, 120]
        For equal influence: individual=4.0, overall=1.0
    """
    individual:  float = 1.0
    operator:    float = 1.0
    overall:     float = 1.0
    # Add new rule weight fields here with defaults.
    # Existing scenario YAMLs that omit the field will use the default.
    # electricity: float = 1.0   ← uncomment when ElectricityCostRule is added


# ── 6. SCENARIO MODEL ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Scenario:
    """
    Combined runtime model assembled by the loader from world + scenario YAML files.
    This is the only object the engine, planner, and rules ever see.

    Note: `route` is a RouteProvider, NOT a raw List[RouteSegment].  [R-17 fix]
    All segment-level data (positions, distances) is accessed via route methods.
    The engine never iterates raw segments.
    """
    meta:      Dict[str, str]
    route:     RouteProvider           # replaces segments: List[RouteSegment]
    physics:   Physics
    stations:  List[Station]
    operators: List[OperatorConfig]
    weights:   Weights
    buses:     List[Bus]

    # ── Convenience accessors (delegate to route) ──

    @property
    def origin(self) -> str:
        """Terminal origin node ID."""
        return self.route.origin

    @property
    def destination(self) -> str:
        """Terminal destination node ID."""
        return self.route.destination

    @property
    def station_ids(self) -> List[str]:
        """All intermediate charging station IDs."""
        return self.route.get_station_ids()

    # ── Lookup helpers ──

    def get_operator(self, operator_id: str) -> Optional[OperatorConfig]:
        """Return the OperatorConfig for operator_id, or None if not found."""
        return next((o for o in self.operators if o.id == operator_id), None)

    def get_station(self, station_id: str) -> Optional[Station]:
        """Return the Station for station_id, or None if not found."""
        return next((s for s in self.stations if s.id == station_id), None)


# ── 7. SIMULATION STATE MODELS ────────────────────────────────────────────────

@dataclass
class ChargingEvent:
    """
    Record of one completed charging stop for a bus.
    Created in engine._start_charging() when charging begins.
    Immutable after creation — append to BusState.completed_events only.
    """
    station_id:   str
    arrival_time: float    # minutes from midnight when bus arrived at station
    wait_time:    float    # minutes in queue before charging started
    charge_start: float    # minutes from midnight when charging began
    charge_end:   float    # minutes from midnight when charging completed


@dataclass
class BusState:
    """
    Mutable live state for one bus throughout the simulation.

    Lifecycle:
        1. Initialised in engine.run_simulation() before event loop.
        2. Updated by _handle_arrival() and _handle_charge_complete() during the loop.
        3. Read by _build_timetables() after the loop to construct output.

    Key invariants:
        - current_time always reflects the simulation time of the last event this bus processed.
        - current_range_km is decremented after each travel segment, reset to battery_range_km after charge.
        - charging_plan is the pre-assigned sequence of stations (V1 static plan).
          In V2 (R-16 fix), this becomes a log of stations visited, not a prescription.
        - current_plan_index tracks position in charging_plan to avoid O(N) .index() calls.  [R-05 fix]
    """
    bus:                   Bus
    charging_plan:         List[str]         # ordered station IDs (static in V1)
    current_range_km:      float             # remaining battery km at current simulation time
    current_time:          float             # simulation time in minutes from midnight
    position:              str               # current node ID (station or terminal)
    station_arrival_time:  float = 0.0       # when bus arrived at current station [R-02 fix]
    current_plan_index:    int   = 0         # index into charging_plan [R-05 fix]
    completed_events:      List[ChargingEvent] = field(default_factory=list)
    total_wait_time:       float = 0.0       # cumulative wait minutes across all stops
    done:                  bool  = False     # True once bus reaches its final destination


@dataclass
class ChargerState:
    """
    Mutable runtime state of one physical charger.

    Note: availability_from/until are converted to float minutes at load time
    so that can_charge_at() avoids string parsing on every call.  [R-25 fix]
    """
    charger_id:      str
    available_from:  float = 0.0       # converted to minutes-from-midnight at load time
    available_until: float = 1440.0    # converted to minutes-from-midnight at load time
    is_operational:  bool  = True      # set to False if charger fails mid-run (FC-06, FC-25)
    free_at:         float = 0.0       # simulation time when this charger next becomes free

    def can_charge_at(self, t: float) -> bool:
        """
        Return True if this charger is operational, within its availability window, and free.
        Uses time-of-day normalisation so multi-day simulations work correctly.  [R-25 fix]

        Args:
            t: simulation time in minutes from midnight (may be > 1440 for Day 2+)
        """
        day_t: float = t % MINUTES_PER_DAY
        return (
            self.is_operational
            and self.available_from <= day_t <= self.available_until
            and self.free_at <= t
        )


@dataclass
class StationState:
    """
    Mutable runtime state of a charging station: charger pool + waiting queue + charge log.

    Design notes:
        - waiting_queue holds bus IDs, not BusState objects, to avoid reference cycles.
        - charge_log is append-only; used by _build_station_logs() to produce output.
        - get_free_charger_at() and get_earliest_free_charger() are used by the engine
          and must never mutate state — they return references for the caller to update.
    """
    station_id:     str
    charger_states: List[ChargerState]
    waiting_queue:  List[str]            = field(default_factory=list)  # bus IDs in arrival order
    charge_log:     List[Dict]           = field(default_factory=list)  # completed charges (for output)

    def get_free_charger_at(self, t: float) -> Optional[ChargerState]:
        """
        Return any charger that can charge at time t, or None if all are busy/unavailable.
        Prefers the charger with the smallest free_at (most recently freed) for tie-breaking.
        """
        available: List[ChargerState] = [c for c in self.charger_states if c.can_charge_at(t)]
        return min(available, key=lambda c: c.free_at) if available else None

    def get_earliest_free_charger(self) -> ChargerState:
        """
        Return whichever charger becomes free soonest (regardless of current time).
        Called when scheduling a queued bus — we want the charger that frees up next.
        """
        return min(self.charger_states, key=lambda c: c.free_at)

    def has_operational_charger(self) -> bool:
        """True if at least one charger is operational (ignoring queue/availability window)."""
        return any(c.is_operational for c in self.charger_states)


# ── 8. SCHEDULE CONTEXT ───────────────────────────────────────────────────────

@dataclass
class ScheduleContext:
    """
    Shared read-mostly view of global simulation state, passed to every rule on every scoring call.

    Created ONCE before the event loop. current_time is updated in-place as events are processed.
    [R-08 fix: moved from engine.py to eliminate circular import]
    [R-09 fix: created once, not per-event]

    Why mutable?
        current_time must reflect "now" for every rule call. Making it immutable would require
        creating a new context on every event — O(N) allocation for N events (R-09).

    Thread safety: single-threaded simulation, not a concern.
    """
    scenario:        Scenario
    bus_states:      Dict[str, BusState]
    station_states:  Dict[str, StationState]
    current_time:    float
    # Real-time priority overrides: bus_id → multiplier applied on top of bus.weight (FC-27)
    priority_overrides: Dict[str, float] = field(default_factory=dict)

    @property
    def time_of_day(self) -> float:
        """
        Current simulation time normalised to [0, 1440) minutes.  [R-22 fix]

        Use this in rules that care about time of day (electricity tariff, charger windows,
        route availability). Do NOT use current_time directly for these checks — it may
        be > 1440 on multi-day simulations.
        """
        return self.current_time % MINUTES_PER_DAY

    def get_operator_delays(self, operator_id: str) -> List[float]:
        """
        Return historical wait times of buses from this operator that have completed at least
        one charge stop. Uses only completed history to avoid circular scoring.

        Performance: O(B) where B = total buses. For 500+ buses, upgrade to an incremental
        cache on ScheduleContext (FC-18).
        """
        return [
            bs.total_wait_time
            for bs in self.bus_states.values()
            if bs.bus.operator == operator_id and len(bs.completed_events) > 0
        ]

    def get_remaining_distance(self, bus_state: BusState) -> float:
        """
        Distance in km from this bus's current position to its final destination.

        Uses RouteProvider directly — no import from planner.  [R-19 fix]
        Works correctly for both BK and KB directions.
        """
        positions: Dict[str, float] = self.scenario.route.get_node_positions(
            bus_state.bus.direction
        )
        dest: str = (
            self.scenario.destination if bus_state.bus.direction == "BK"
            else self.scenario.origin
        )
        return positions[dest] - positions[bus_state.position]

    def set_priority_override(self, bus_id: str, multiplier: float) -> None:
        """
        Apply a real-time priority multiplier to a specific bus (FC-27).
        Called by external API or Streamlit UI override button.
        """
        self.priority_overrides[bus_id] = multiplier


# ── 9. OUTPUT MODELS ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BusTimetable:
    """Per-bus output: departure, charging events, arrival, total wait."""
    bus_id:          str
    operator:        str
    direction:       str
    departure_time:  float               # minutes from midnight
    charging_plan:   List[str]           # station IDs in visit order
    charging_events: List[ChargingEvent]
    total_wait_time: float
    arrival_time:    float               # at destination, minutes from midnight
    total_trip_time: float               # arrival_time - departure_time


@dataclass(frozen=True)
class StationChargeLog:
    """Per-station output: ordered log of all charging sessions."""
    station_id: str
    entries:    List[Dict]     # sorted by charge_start; each: bus_id/operator/times/wait/charger_id


@dataclass(frozen=True)
class SimulationResult:
    """
    Complete output of one simulation run.  [R-21 fix]

    Returned by engine.run_simulation() instead of a bare tuple.
    Adding new aggregate fields here requires zero changes to call sites —
    they access fields by name, not positional index.

    Fields:
        scenario_id:                  From scenario.meta['id'].
        bus_timetables:               One entry per bus, sorted by (direction, departure_time).
        station_logs:                 One entry per charging station, sorted by charge_start.
        total_network_wait_minutes:   Sum of all bus wait times across the whole run.
        per_operator_avg_wait:        operator_id → average wait minutes for that operator's fleet.
        simulation_duration_minutes:  Time from first departure to last arrival.
        max_single_bus_wait_minutes:  Worst-case individual bus total wait time.
    """
    scenario_id:                  str
    bus_timetables:               List[BusTimetable]
    station_logs:                 List[StationChargeLog]
    total_network_wait_minutes:   float
    per_operator_avg_wait:        Dict[str, float]
    simulation_duration_minutes:  float
    max_single_bus_wait_minutes:  float
```

---

## 4. Routes — `scheduler/routes/linear.py`

```python
"""
scheduler/routes/linear.py
===========================
LinearRouteProvider: RouteProvider implementation for a single ordered segment list.

This is the V1 (current) implementation. All node positions are pre-computed once
at construction time — every query method is O(1) or O(stations).

For a graph-based multi-route implementation (V2), see FUTURE_CHANGES.md FC-22.
The engine, planner, and rules require zero changes to use GraphRouteProvider —
swap the implementation in loader._parse_world().
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from scheduler.models import RouteProvider, RouteSegment


class LinearRouteProvider(RouteProvider):
    """
    RouteProvider for a single directed linear sequence of road segments.

    Concretely: Bengaluru → A → B → C → D → Kochi.
    Positions are pre-computed for both BK (left-to-right) and KB (right-to-left)
    directions at construction time.

    Assumptions:
        - Segments form a contiguous chain: seg[i].to_node == seg[i+1].from_node.
        - No segment is shorter than 1km (floating point safety).
        - Directions are exactly 'BK' (origin→destination) or 'KB' (destination→origin).
    """

    def __init__(self, segments: List[RouteSegment], station_ids: List[str]) -> None:
        """
        Args:
            segments:    Ordered list of RouteSegment objects forming the full path.
            station_ids: IDs of intermediate charging stations (not origin/destination).
        """
        self._segments:    List[RouteSegment]  = segments
        self._station_ids: List[str]           = station_ids

        # Pre-compute BK positions once at construction
        self._bk_positions, self._total = self._compute_bk_positions(segments)

        # KB positions are the mirror: distance from destination instead of origin
        self._kb_positions: Dict[str, float] = {
            node: self._total - dist
            for node, dist in self._bk_positions.items()
        }

    @staticmethod
    def _compute_bk_positions(
        segments: List[RouteSegment],
    ) -> Tuple[Dict[str, float], float]:
        """
        Walk the segment list once and record each node's cumulative distance from origin.

        Returns:
            (positions dict, total_distance_km)
        """
        positions: Dict[str, float] = {}
        d: float = 0.0
        for seg in segments:
            positions[seg.from_node] = d
            d += seg.distance_km
        positions[segments[-1].to_node] = d
        return positions, d

    @property
    def origin(self) -> str:
        return self._segments[0].from_node

    @property
    def destination(self) -> str:
        return self._segments[-1].to_node

    def get_node_positions(self, direction: str) -> Dict[str, float]:
        """
        Return pre-computed {node_id: distance_from_direction_origin}.

        BK: Bengaluru=0, Kochi=540
        KB: Kochi=0, Bengaluru=540 (mirror image)
        """
        return self._bk_positions if direction == "BK" else self._kb_positions

    def get_station_ids(self) -> List[str]:
        """Return intermediate charging station IDs (not origin or destination)."""
        return list(self._station_ids)

    def get_total_distance(self) -> float:
        """Total route distance in km."""
        return self._total

    def get_next_reachable_stations(
        self,
        from_node: str,
        direction: str,
        range_km: float,
    ) -> List[str]:
        """
        Return stations reachable forward from from_node within range_km.

        'Forward' means increasing position value in the given direction.
        Stations are returned sorted by distance from from_node (nearest first).

        Used for:
          - Planner: validating charging plans against battery range.
          - Engine (V2/R-16): JIT re-routing after charger failure.

        Args:
            from_node:  Current node ID.
            direction:  'BK' or 'KB'.
            range_km:   Remaining battery range in km.

        Returns:
            List of reachable station IDs, nearest first.
        """
        positions: Dict[str, float] = self.get_node_positions(direction)
        from_pos: float = positions[from_node]
        reachable: List[Tuple[float, str]] = [
            (positions[sid] - from_pos, sid)
            for sid in self._station_ids
            if 0 < positions[sid] - from_pos <= range_km
        ]
        reachable.sort()
        return [sid for _, sid in reachable]
```

---

## 5. Loader — `scheduler/loader.py`

```python
"""
scheduler/loader.py
===================
YAML → Scenario: loads world and scenario YAML files, validates them, and assembles
a Scenario object for the engine to consume.

Loader is the only module that knows about file formats. The engine, rules, and planner
never read YAML — they receive a fully validated Scenario.

Two-file loading flow:  [R-18 fix]
    load_scenario(scenario_path)
        → _load_scenario_config(scenario_path)   reads buses, weights, operators
        → _load_world(world_dir/world_id.yaml)   reads route, stations, physics
        → _merge(config, world)                  assembles Scenario

Validation:
    _validate_scenario(): checks required YAML keys and valid directions.  [R-14 fix]
    Raises ValueError with a descriptive message (not a KeyError) on malformed input.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from scheduler.models import (
    Bus, Charger, ChargerState, DriverShift, OperatorConfig, Physics,
    RouteSegment, Scenario, Station, Weights, VALID_DIRECTIONS,
    time_str_to_minutes, MINUTES_PER_DAY,
)
from scheduler.routes.linear import LinearRouteProvider


def load_scenario(path: str, world_dir: str = "world") -> Scenario:
    """
    Load a scenario YAML and its referenced world YAML, returning a Scenario.

    Args:
        path:      Path to the scenario YAML file.
        world_dir: Directory containing world YAML files. Defaults to 'world/'.

    Raises:
        ValueError: If either YAML is malformed or contains invalid values.
        FileNotFoundError: If the scenario or world file does not exist.
    """
    with open(path, "r") as f:
        scenario_data: Dict[str, Any] = yaml.safe_load(f)
    _validate_scenario(scenario_data)

    world_id: str = scenario_data["meta"]["world_id"]
    world_path: str = str(Path(world_dir) / f"{world_id}.yaml")
    with open(world_path, "r") as f:
        world_data: Dict[str, Any] = yaml.safe_load(f)
    _validate_world(world_data)

    return _assemble_scenario(scenario_data, world_data)


def list_scenarios(folder: str = "scenarios") -> List[Path]:
    """
    Return sorted list of scenario YAML paths in the given folder.
    Used by the Streamlit UI to populate the scenario dropdown.
    """
    return sorted(Path(folder).glob("scenario_*.yaml"))


# ── PRIVATE: VALIDATION ────────────────────────────────────────────────────────

def _validate_scenario(data: Dict[str, Any]) -> None:
    """
    Validate scenario YAML structure.  [R-14 fix]
    Raises ValueError with a descriptive message on any missing or invalid field.
    """
    required: List[str] = ["meta", "operators", "weights", "buses"]
    for key in required:
        if key not in data:
            raise ValueError(f"Scenario YAML missing required key: '{key}'")
    if "world_id" not in data.get("meta", {}):
        raise ValueError("Scenario YAML meta block must include 'world_id'.")
    for bus in data["buses"]:
        if bus.get("direction") not in VALID_DIRECTIONS:
            raise ValueError(
                f"Bus '{bus.get('id', '?')}' has invalid direction "
                f"'{bus.get('direction')}'. Must be one of {VALID_DIRECTIONS}."
            )


def _validate_world(data: Dict[str, Any]) -> None:
    """
    Validate world YAML structure.
    Raises ValueError with a descriptive message on any missing key.
    """
    required: List[str] = ["id", "route", "physics", "stations"]
    for key in required:
        if key not in data:
            raise ValueError(f"World YAML missing required key: '{key}'")
    if "segments" not in data["route"]:
        raise ValueError("World YAML route block must include 'segments'.")


# ── PRIVATE: PARSING AND ASSEMBLY ─────────────────────────────────────────────

def _assemble_scenario(
    scenario_data: Dict[str, Any],
    world_data:    Dict[str, Any],
) -> Scenario:
    """
    Parse world + scenario YAML dicts and assemble a validated Scenario object.

    World data: route, physics, stations.
    Scenario data: meta, operators, weights, buses.

    The route type field ('linear' or 'graph') selects the RouteProvider implementation.
    Currently only 'linear' is implemented; 'graph' raises NotImplementedError (FC-22).
    """
    # ── Route ──
    segments: List[RouteSegment] = [
        RouteSegment(s["from"], s["to"], float(s["distance_km"]))
        for s in world_data["route"]["segments"]
    ]
    station_ids_from_world: List[str] = [
        st["id"] for st in world_data["stations"]
    ]
    route_type: str = world_data["route"].get("type", "linear")
    if route_type == "linear":
        route = LinearRouteProvider(segments=segments, station_ids=station_ids_from_world)
    else:
        raise NotImplementedError(
            f"Route type '{route_type}' is not yet implemented. "
            f"See FUTURE_CHANGES.md FC-22 for GraphRouteProvider."
        )

    # ── Physics ──
    p = world_data["physics"]
    physics = Physics(
        battery_range_km    = float(p["battery_range_km"]),
        charge_time_minutes = int(p["charge_time_minutes"]),
        travel_speed_kmh    = float(p["travel_speed_kmh"]),
        charge_to_full      = bool(p.get("charge_to_full", True)),
    )

    # ── Stations ──
    # Note: available_from/until are pre-converted to float minutes for ChargerState
    # so can_charge_at() avoids string parsing on every call.  [R-25 fix]
    stations: List[Station] = [
        Station(
            id=st["id"],
            chargers=tuple(
                Charger(
                    id=c["id"],
                    operational=bool(c["operational"]),
                    available_from=c.get("available_from", "00:00"),
                    available_until=c.get("available_until", "23:59"),
                )
                for c in st["chargers"]
            ),
        )
        for st in world_data["stations"]
    ]

    # ── Operators ──
    operators: List[OperatorConfig] = [
        OperatorConfig(o["id"], o["display_name"], float(o.get("weight", 1.0)))
        for o in scenario_data["operators"]
    ]

    # ── Weights ──
    w = scenario_data["weights"]
    weights = Weights(
        individual = float(w.get("individual", 1.0)),
        operator   = float(w.get("operator",   1.0)),
        overall    = float(w.get("overall",     1.0)),
    )

    # ── Buses ──
    buses: List[Bus] = []
    for b in scenario_data["buses"]:
        ds = b.get("driver_shift")
        shift: Optional[DriverShift] = DriverShift(ds["start"], ds["end"]) if ds else None
        buses.append(Bus(
            id             = b["id"],
            operator       = b["operator"],
            direction      = b["direction"],
            departure      = b["departure"],
            priority_class = b.get("priority_class", "standard"),
            weight         = float(b.get("weight", 1.0)),
            charge_strategy= b.get("charge_strategy", "full"),
            driver_shift   = shift,
        ))

    return Scenario(
        meta      = {"id": world_data["id"], **dict(scenario_data["meta"])},
        route     = route,
        physics   = physics,
        stations  = stations,
        operators = operators,
        weights   = weights,
        buses     = buses,
    )


def _build_charger_state(charger: Charger) -> ChargerState:
    """
    Convert a frozen Charger config into a mutable ChargerState for simulation.
    Pre-converts availability strings to float minutes.  [R-25 fix]
    """
    return ChargerState(
        charger_id      = charger.id,
        available_from  = time_str_to_minutes(charger.available_from),
        available_until = time_str_to_minutes(charger.available_until),
        is_operational  = charger.operational,
        free_at         = 0.0,
    )
```

---

## 6. Planner — `scheduler/planner.py`

```python
"""
scheduler/planner.py
====================
Valid charging plan generator. Enumerates all charging station sequences that satisfy
the battery range constraint, then assigns buses to plans via round-robin distribution.

Key design decisions:
  - Planner uses RouteProvider, not raw segments.  [R-17 fix]
  - All position data is accessed via route.get_node_positions() — O(1) lookups.
  - Round-robin assignment spreads buses across valid plans.  [R-10 fix]
  - get_valid_charging_plans() is deterministic and pure (no side effects).

V2 upgrade path:
  - Pass live StationState queue depths to select_charging_plan() for congestion-aware selection.
  - Replace static plan assignment with JIT evaluation via route.get_next_reachable_stations().
    (See R-16, FC-25 — requires engine change, not planner change.)
"""

from __future__ import annotations

from itertools import combinations
from typing import Dict, List, NamedTuple

from scheduler.models import RouteProvider, Scenario


class RoutePositions(NamedTuple):
    """
    Named return type for position lookups.  [R-15 fix]

    Attributes:
        positions:      {node_id: cumulative_distance_km_from_origin}
        total_distance: Full route length in km.
    """
    positions:      Dict[str, float]
    total_distance: float


def get_valid_charging_plans(direction: str, scenario: Scenario) -> List[List[str]]:
    """
    Enumerate all valid charging station sequences for the given direction.

    A plan is valid if no gap between consecutive checkpoints exceeds battery_range_km.
    Checkpoints = [origin, *plan_stations, destination].

    Every subset of intermediate stations is tested; the result includes all valid subsets
    from minimum-stop to all-stops.

    Args:
        direction: 'BK' or 'KB'.
        scenario:  Loaded scenario (route and physics accessed from here).

    Returns:
        List of valid station-ID lists. Each list is a valid charging plan.
        Stations within each plan are in route order (sorted by position).
    """
    route: RouteProvider = scenario.route
    positions: Dict[str, float] = route.get_node_positions(direction)
    battery: float = scenario.physics.battery_range_km
    total: float = route.get_total_distance()

    # Sort intermediate stations by position in this direction (nearest to origin first)
    station_ids: List[str] = sorted(
        route.get_station_ids(),
        key=lambda sid: positions[sid]
    )

    valid: List[List[str]] = []
    for size in range(1, len(station_ids) + 1):
        for combo in combinations(station_ids, size):
            plan: List[str] = list(combo)
            if _is_valid_plan(plan, positions, battery, total):
                valid.append(plan)
    return valid


def _is_valid_plan(
    stations:      List[str],
    positions:     Dict[str, float],
    battery_range: float,
    total_distance: float,
) -> bool:
    """
    Return True if every gap between consecutive checkpoints is ≤ battery_range.

    Checkpoints include: 0.0 (origin) + station positions + total_distance (destination).
    Assumes a full charge is restored at each intermediate station.

    Args:
        stations:       Station IDs in route order.
        positions:      Pre-computed {node_id: distance} from RouteProvider.
        battery_range:  Max range on a full charge (km).
        total_distance: Full route length (km).
    """
    checkpoints: List[float] = (
        [0.0]
        + [positions[s] for s in stations]
        + [total_distance]
    )
    return all(
        checkpoints[i + 1] - checkpoints[i] <= battery_range
        for i in range(len(checkpoints) - 1)
    )


def select_charging_plan(
    bus_index: int,
    direction: str,
    scenario:  Scenario,
) -> List[str]:
    """
    Select a charging plan for one bus.

    Strategy:
      1. Get all valid plans (may be 3–10 depending on route/battery).
      2. Keep only minimum-stop plans (fewest stops = least overhead).
      3. Distribute buses round-robin across candidates to avoid convergence.  [R-10 fix]

    The round-robin ensures that bus-BK-01 gets plan (A,C), bus-BK-02 gets (B,C),
    bus-BK-03 gets (B,D), bus-BK-04 cycles back to (A,C), etc.

    Args:
        bus_index: 0-based ordinal of this bus within its direction (BK or KB).
                   Used as the round-robin index.
        direction: 'BK' or 'KB'.
        scenario:  Loaded scenario.

    Raises:
        ValueError: If no valid charging plan exists for this direction and battery range.
    """
    valid: List[List[str]] = get_valid_charging_plans(direction, scenario)
    if not valid:
        raise ValueError(
            f"No valid charging plans for direction '{direction}'. "
            f"Check battery_range_km vs route distances."
        )
    min_stops: int = min(len(p) for p in valid)
    candidates: List[List[str]] = [p for p in valid if len(p) == min_stops]
    return candidates[bus_index % len(candidates)]
```

---

## 7. Rules — `scheduler/rules/`

### `rules/base.py`

```python
"""
scheduler/rules/base.py
========================
Abstract base classes for the pluggable rule engine.

SoftRule: contributes a float priority score during queue arbitration.
HardRule: binary constraint used by the planner to validate charging plans.

Adding a new rule:
  - Soft: subclass SoftRule, implement score(), register in app.py.
  - Hard: subclass HardRule, implement is_satisfied(), call in planner._is_valid_plan().
  Neither change touches the engine or the scoring formula.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from scheduler.models import BusState, ScheduleContext, RoutePositions


class SoftRule(ABC):
    """
    Contributes a float priority score during charger queue arbitration.

    Score semantics:
        Higher score → bus gets the charger sooner.
        Returning 0.0 means this rule has no opinion.
        Negative scores are valid (e.g., penalise peak-hour charging).

    Scores combine additively in WeightedScorer:
        final = priority_override × bus.weight × Σ(weight_k × rule_k.score())

    Scale contract (see OVERVIEW.md §8):
        IndividualWaitRule:       [0, ~120]
        OverallThroughputRule:    [100, 540]
        DriverShiftProximityRule: [0, ~300]
        HeadwayRule:              [-100, 0]
    Equal weights give OverallThroughputRule 4–5× more influence. Document and own this.
    """
    name: str = "unnamed_soft_rule"

    @abstractmethod
    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        """
        Return a float priority score for bus_state at the current simulation time.
        Must not modify bus_state or context.
        """
        ...


class HardRule(ABC):
    """
    Binary constraint. Violated plans are rejected at planning time (before simulation).
    Hard rules do not affect queue priority — they gate plan validity.

    Hard rules operate on station lists and pre-computed positions,
    not on live BusState — they run before the simulation loop.
    """
    name: str = "unnamed_hard_rule"

    @abstractmethod
    def is_satisfied(
        self,
        stations:      List[str],
        rp:            RoutePositions,
        battery_range: float,
    ) -> bool:
        """
        Return True if this charging plan satisfies this constraint.

        Args:
            stations:      Ordered station IDs in the candidate plan.
            rp:            Pre-computed positions for the bus's direction.
            battery_range: Max range on a full charge (km).
        """
        ...
```

### `rules/hard_rules.py`

```python
"""
scheduler/rules/hard_rules.py
==============================
Hard rules: binary constraints that filter invalid charging plans.
Called by planner._is_valid_plan() before the simulation begins.
"""

from __future__ import annotations

from typing import List

from scheduler.models import RoutePositions
from scheduler.rules.base import HardRule


class RangeConstraint(HardRule):
    """
    Hard Rule: no gap between consecutive checkpoints may exceed battery_range_km.

    This is the primary physical constraint — every charging plan must satisfy it.
    A bus that violates this rule would run out of battery before reaching the next station.
    """
    name: str = "range_constraint"

    def is_satisfied(
        self,
        stations:      List[str],
        rp:            RoutePositions,
        battery_range: float,
    ) -> bool:
        checkpoints: List[float] = (
            [0.0]
            + [rp.positions[s] for s in stations]
            + [rp.total_distance]
        )
        return all(
            checkpoints[i + 1] - checkpoints[i] <= battery_range
            for i in range(len(checkpoints) - 1)
        )


class StationOrderConstraint(HardRule):
    """
    Hard Rule: stations must appear in route order (monotonically increasing position).

    Prevents nonsensical plans where a bus would backtrack to a station it passed.
    In a linear route this is guaranteed by the combination generator if stations are
    pre-sorted — but this rule makes the constraint explicit and testable.
    """
    name: str = "station_order"

    def is_satisfied(
        self,
        stations:      List[str],
        rp:            RoutePositions,
        battery_range: float,
    ) -> bool:
        distances: List[float] = [rp.positions[s] for s in stations]
        return distances == sorted(distances)
```

### `rules/soft_rules.py`

```python
"""
scheduler/rules/soft_rules.py
==============================
Soft rules: priority scoring functions called during charger queue arbitration.

Each rule returns a float. Higher score = bus charges sooner.
Rules combine via WeightedScorer using scenario-configured weights.

Adding a new rule: create a class extending SoftRule, implement score(),
register in app.py with a weight key. Zero engine changes.

Current rules:
  IndividualWaitRule        — prevents starvation of individual buses
  OperatorFairnessRule      — prevents systematic disadvantage of operator fleets
  OverallThroughputRule     — prioritises buses with more downstream journey remaining
  DriverShiftProximityRule  — escalates priority as shift end approaches journey end
"""

from __future__ import annotations

from typing import List, TYPE_CHECKING

from scheduler.models import driver_shift_end_minutes
from scheduler.rules.base import SoftRule

if TYPE_CHECKING:
    from scheduler.models import BusState, ScheduleContext


class IndividualWaitRule(SoftRule):
    """
    Prevents any single bus from being indefinitely starved at a station.

    Score = minutes this bus has been waiting at the current station since arrival.
    Natural range: [0, ~120 minutes].

    Weight guidance: IndividualWait range is ~4× smaller than OverallThroughput.
    Set individual=4.0 to equalise influence with overall=1.0.
    """
    name: str = "individual_wait"

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        """
        Return how long (minutes) this bus has been waiting at its current station.
        A bus that arrived 60 minutes ago scores 60; one that just arrived scores 0.
        """
        wait_so_far: float = context.current_time - bus_state.station_arrival_time
        return max(0.0, wait_so_far)


class OperatorFairnessRule(SoftRule):
    """
    Ensures no operator's fleet is systematically disadvantaged.

    Score = operator_weight × (average total wait of this operator's completed buses).
    Uses HISTORICAL data only (buses with at least one completed charge stop)
    to avoid circular scoring feedback.

    Natural range: [0, ~200] (depends on operator weight and average delays).
    """
    name: str = "operator_fairness"

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        """
        If KPN's buses have averaged 40 min wait and KPN has weight 2.0,
        this rule contributes 80 points to every waiting KPN bus's score.
        Buses from underserved operators are boosted relative to well-served ones.
        """
        operator_id: str  = bus_state.bus.operator
        op_config         = context.scenario.get_operator(operator_id)
        op_weight: float  = op_config.weight if op_config else 1.0
        delays: List[float] = context.get_operator_delays(operator_id)
        avg_delay: float  = sum(delays) / len(delays) if delays else 0.0
        return op_weight * avg_delay


class OverallThroughputRule(SoftRule):
    """
    Minimises total network trip time by prioritising buses with more journey ahead.

    Score = remaining travel time from current station to destination (minutes).
    A bus closer to the start of its journey has more "downstream impact" if delayed.

    Natural range: [~100, ~540 minutes].

    NOTE: This rule naturally dominates at equal weights (4–5× larger than IndividualWait).
    See REFUTE.md R-07 and OVERVIEW.md §8 for compensating weight guidance.
    """
    name: str = "overall_throughput"

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        """
        Convert remaining km to travel minutes.
        Uses ScheduleContext.get_remaining_distance() which reads RouteProvider directly.
        """
        remaining_km: float = context.get_remaining_distance(bus_state)
        speed: float        = context.scenario.physics.travel_speed_kmh
        return (remaining_km / speed) * 60.0


class DriverShiftProximityRule(SoftRule):
    """
    Escalates priority for buses whose driver's shift is approaching its end.  [R-23 fix]

    Score increases non-linearly as remaining shift time approaches remaining journey time.
    Buses with no shift constraint always score 0.0 from this rule.

    Natural range: [0, ~300 urgency-units].
    Urgency activates when shift_remaining < 1.5 × journey_remaining (minutes).

    Weight guidance: start at weight=1.0. Only buses with driver_shift constraints are affected.
    """
    name: str = "shift_proximity"

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        """
        Return 0 if no driver shift. Otherwise, compute urgency based on how close
        the shift end is relative to remaining journey time. Higher urgency = higher score.
        """
        if not bus_state.bus.driver_shift:
            return 0.0

        shift_end: float        = driver_shift_end_minutes(bus_state.bus.driver_shift)
        shift_remaining: float  = shift_end - context.current_time
        remaining_km: float     = context.get_remaining_distance(bus_state)
        speed: float            = context.scenario.physics.travel_speed_kmh
        journey_remaining: float = (remaining_km / speed) * 60.0

        if shift_remaining <= 0 or journey_remaining <= 0:
            return 300.0   # maximum urgency: shift already exceeded or journey complete

        # urgency_ratio: 1.0 means shift ends exactly when journey ends
        # Score is 0 below 0.5 threshold, rises linearly above it
        urgency_ratio: float = journey_remaining / max(shift_remaining, 1.0)
        return max(0.0, (urgency_ratio - 0.5) * 200.0)
```

---

## 8. Scoring — `scheduler/scoring.py`

```python
"""
scheduler/scoring.py
=====================
WeightedScorer: combines registered soft rules into a single priority float.

The scorer is the only place the priority formula is written.
Rules are injected at construction — the scorer never names them directly.
Adding a rule = add one (rule, weight_key) pair to the rules list. No scorer changes.

Formula:
    score(bus) = priority_override × bus.weight × Σ_k( weights[k] × rule_k.score(bus, ctx) )

Score range: unbounded float. Higher = bus charges sooner.
Typical range for standard 3-rule config: roughly [50, 700].
"""

from __future__ import annotations

from typing import List, Tuple, TYPE_CHECKING

from scheduler.models import Weights

if TYPE_CHECKING:
    from scheduler.models import BusState, ScheduleContext
    from scheduler.rules.base import SoftRule


class WeightedScorer:
    """
    Aggregates registered soft rules into one priority score per bus.

    Construction:
        Pass weights (from Scenario) and a list of (SoftRule, weight_key) pairs.
        weight_key is the attribute name on Weights to read for this rule's coefficient.

    Usage:
        scorer.score(bus_state, context) → float

    Scale note:
        IndividualWaitRule ranges [0, 120]; OverallThroughputRule ranges [100, 540].
        Equal weights (1.0/1.0) give OverallThroughputRule ~4× more influence by scale.
        For equal effective influence: individual=4.0, overall=1.0. See REFUTE.md R-07.
    """

    def __init__(
        self,
        weights: Weights,
        rules:   List[Tuple[SoftRule, str]],
    ) -> None:
        """
        Args:
            weights: Scenario-level weight configuration (from scenario YAML).
            rules:   List of (SoftRule instance, weight_attribute_name_on_Weights).
                     Example: [(IndividualWaitRule(), "individual")]
        """
        self.weights: Weights                   = weights
        self.rules:   List[Tuple[SoftRule, str]] = rules

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        """
        Compute priority score for one bus at the current simulation time.

        Applies priority_override from context (real-time escalation — FC-27),
        then multiplies bus.weight, then sums weighted rule scores.

        Args:
            bus_state: Current state of the bus being scored.
            context:   Global simulation state at current_time.

        Returns:
            Float priority score. Higher = this bus charges sooner when a charger frees up.
        """
        priority_override: float = context.priority_overrides.get(bus_state.bus.id, 1.0)
        rule_total: float = sum(
            getattr(self.weights, weight_key, 1.0) * rule.score(bus_state, context)
            for rule, weight_key in self.rules
        )
        return priority_override * bus_state.bus.weight * rule_total
```

---

## 9. Engine — `scheduler/engine.py`

*(Engine code is substantial; comments on each function are the primary update. Key signatures below.)*

```python
"""
scheduler/engine.py
====================
Discrete-event simulation engine.

Entry point: run_simulation(scenario) → SimulationResult  [R-21 fix]

Event types:
    BUS_ARRIVES_AT_STATION  — bus reaches a charging station
    CHARGING_COMPLETE       — a charging session ends

Event processing:
    Events are stored in a min-heap sorted by (time, sequence).  [R-06 fix]
    sequence is a monotonically increasing tie-breaker for simultaneous events.

Engine invariants:
    - ScheduleContext is created ONCE before the event loop.  [R-09 fix]
    - context.current_time is updated in-place at the start of each event.
    - node_positions are pre-computed once via RouteProvider, not per event.  [R-03 fix]
    - current_range_km is decremented after every travel segment.  [R-04 fix]

Known V1 limitation (R-16):
    Charging plans are assigned statically before the simulation begins.
    If a charger fails mid-run, the bus will arrive at a non-functional station
    and stall. V2 fix: JIT routing via RouteProvider.get_next_reachable_stations().

Dependency:
    engine.py is the top of the import hierarchy. It imports from:
      models, planner, scoring, rules/soft_rules, routes (indirectly via Scenario).
    Nothing imports from engine.py except app.py and tests.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

from scheduler.models import (
    BusState, BusTimetable, ChargerState, ChargingEvent,
    Scenario, ScheduleContext, SimulationResult, StationChargeLog, StationState,
    minutes_to_time_str, time_str_to_minutes,
)
from scheduler.planner import select_charging_plan
from scheduler.scoring import WeightedScorer
from scheduler.rules.soft_rules import (
    IndividualWaitRule, OperatorFairnessRule, OverallThroughputRule, DriverShiftProximityRule,
)


class EventType(Enum):
    BUS_ARRIVES_AT_STATION = auto()
    CHARGING_COMPLETE      = auto()


@dataclass(order=True)
class Event:
    """
    One simulation event. Sorted by (time, sequence) for deterministic ordering.  [R-06 fix]

    Fields:
        time:       Simulation time this event fires (minutes from midnight).
        sequence:   Monotonic counter — breaks ties when two events share the same time.
        event_type: What kind of event this is.
        bus_id:     Which bus is involved.
        station_id: At which station this event occurs.
    """
    time:       float
    sequence:   int
    event_type: EventType = field(compare=False)
    bus_id:     str       = field(compare=False)
    station_id: str       = field(compare=False)


def run_simulation(scenario: Scenario) -> SimulationResult:
    """
    Main simulation entry point. Pure function: same scenario input → same output.

    Steps:
      1. Build WeightedScorer with registered soft rules.
      2. Pre-compute node positions via RouteProvider (once, not per event).
      3. Assign charging plans (round-robin across valid minimum-stop plans).
      4. Initialise BusState and StationState for all buses and stations.
      5. Push initial BUS_ARRIVES_AT_STATION events onto the heap.
      6. Create ScheduleContext (once).
      7. Process events until heap is empty.
      8. Build and return SimulationResult.

    Args:
        scenario: Fully loaded and validated Scenario object.

    Returns:
        SimulationResult with timetables, station logs, and aggregate metrics.
    """
    scorer: WeightedScorer = WeightedScorer(
        weights=scenario.weights,
        rules=[
            (IndividualWaitRule(),          "individual"),
            (OperatorFairnessRule(),        "operator"),
            (OverallThroughputRule(),       "overall"),
            (DriverShiftProximityRule(),    "individual"),  # shares weight for now
        ],
    )

    # Pre-compute positions for both directions — used throughout the simulation  [R-03, R-09 fix]
    node_positions: Dict[str, Dict[str, float]] = {
        direction: scenario.route.get_node_positions(direction)
        for direction in ("BK", "KB")
    }

    # Assign charging plans (round-robin distribution)  [R-10 fix]
    bus_plans: Dict[str, List[str]] = {}
    bk_index: int = 0
    kb_index: int = 0
    for bus in scenario.buses:
        idx: int = bk_index if bus.direction == "BK" else kb_index
        bus_plans[bus.id] = select_charging_plan(idx, bus.direction, scenario)
        if bus.direction == "BK":
            bk_index += 1
        else:
            kb_index += 1

    # Initialise bus states
    bus_states: Dict[str, BusState] = {
        bus.id: BusState(
            bus              = bus,
            charging_plan    = bus_plans[bus.id],
            current_range_km = scenario.physics.battery_range_km,
            current_time     = time_str_to_minutes(bus.departure),
            position         = (
                scenario.origin if bus.direction == "BK" else scenario.destination
            ),
        )
        for bus in scenario.buses
    }

    # Initialise station states — only operational chargers get ChargerState
    from scheduler.loader import _build_charger_state
    station_states: Dict[str, StationState] = {
        s.id: StationState(
            station_id     = s.id,
            charger_states = [
                _build_charger_state(c)
                for c in s.chargers if c.operational
            ],
        )
        for s in scenario.stations
    }

    # Build initial events: each bus → first charging stop
    event_queue: List[Event] = []
    seq: int = 0
    for bus in scenario.buses:
        plan: List[str] = bus_plans[bus.id]
        if not plan:
            continue
        bs: BusState = bus_states[bus.id]
        arrival: float = _compute_arrival_time(bs, plan[0], node_positions, scenario.physics.travel_speed_kmh)
        heapq.heappush(event_queue, Event(arrival, seq, EventType.BUS_ARRIVES_AT_STATION, bus.id, plan[0]))
        seq += 1

    # Create ScheduleContext once; update current_time in-place  [R-09 fix]
    context: ScheduleContext = ScheduleContext(
        scenario       = scenario,
        bus_states     = bus_states,
        station_states = station_states,
        current_time   = 0.0,
    )

    # ── Main event loop ───────────────────────────────────────────────────────
    while event_queue:
        event: Event = heapq.heappop(event_queue)
        context.current_time = event.time

        if event.event_type == EventType.BUS_ARRIVES_AT_STATION:
            seq = _handle_arrival(event, context, scorer, event_queue, seq, scenario, node_positions)
        elif event.event_type == EventType.CHARGING_COMPLETE:
            seq = _handle_charge_complete(event, context, scorer, event_queue, seq, scenario, node_positions)

    return _build_result(scenario, bus_states, station_states)


def _handle_arrival(
    event:          Event,
    context:        ScheduleContext,
    scorer:         WeightedScorer,
    event_queue:    List[Event],
    seq:            int,
    scenario:       Scenario,
    node_positions: Dict[str, Dict[str, float]],
) -> int:
    """
    Handle BUS_ARRIVES_AT_STATION.

    Updates the bus's position and arrival time, then either:
    - Starts charging immediately if a charger is free, OR
    - Adds the bus to the station's waiting queue.

    The station_arrival_time is recorded here for IndividualWaitRule.  [R-02 fix]
    """
    bs: BusState     = context.bus_states[event.bus_id]
    ss: StationState = context.station_states[event.station_id]

    bs.position             = event.station_id
    bs.current_time         = event.time
    bs.station_arrival_time = event.time

    free: Optional[ChargerState] = ss.get_free_charger_at(event.time)
    if free:
        seq = _start_charging(event.bus_id, event.station_id, event.time, free,
                               context, event_queue, seq, scenario)
    else:
        ss.waiting_queue.append(event.bus_id)

    return seq


def _handle_charge_complete(
    event:          Event,
    context:        ScheduleContext,
    scorer:         WeightedScorer,
    event_queue:    List[Event],
    seq:            int,
    scenario:       Scenario,
    node_positions: Dict[str, Dict[str, float]],
) -> int:
    """
    Handle CHARGING_COMPLETE.

    1. Scores waiting buses and starts the highest-priority one on the freed charger.
    2. Advances this bus to its next stop (or marks it done if no more stops).

    Plan advancement uses current_plan_index, not plan.index().  [R-05 fix]
    Range is decremented for each travel segment.                 [R-04 fix]
    """
    bs: BusState     = context.bus_states[event.bus_id]
    ss: StationState = context.station_states[event.station_id]
    bs.current_time  = event.time

    # ── Service next queued bus ──
    freed: ChargerState = ss.get_earliest_free_charger()
    if ss.waiting_queue:
        waiting: List[BusState] = [context.bus_states[bid] for bid in ss.waiting_queue]
        scored: List[Tuple[float, str]] = [
            (scorer.score(w, context), w.bus.id) for w in waiting
        ]
        scored.sort(reverse=True)
        next_id: str = scored[0][1]
        ss.waiting_queue.remove(next_id)
        seq = _start_charging(next_id, event.station_id, event.time, freed,
                               context, event_queue, seq, scenario)

    # ── Advance this bus to next stop or destination ──
    plan: List[str] = bs.charging_plan
    idx:  int       = bs.current_plan_index
    positions: Dict[str, float] = node_positions[bs.bus.direction]

    if idx + 1 < len(plan):
        # More charging stops remain
        next_station: str = plan[idx + 1]
        bs.current_plan_index += 1
        dist: float    = positions[next_station] - positions[bs.position]
        bs.current_range_km -= dist              # decrement range  [R-04 fix]
        arrival: float = _compute_arrival_time(bs, next_station, node_positions,
                                                scenario.physics.travel_speed_kmh)
        bs.position     = next_station
        bs.current_time = arrival
        heapq.heappush(event_queue, Event(
            arrival, seq, EventType.BUS_ARRIVES_AT_STATION, event.bus_id, next_station
        ))
        seq += 1
    else:
        # Last charge done — travel to final destination
        dest: str = (
            scenario.destination if bs.bus.direction == "BK" else scenario.origin
        )
        dist = positions[dest] - positions[bs.position]
        bs.current_range_km -= dist              # decrement range for final leg  [R-04 fix]
        arrival = _compute_arrival_time(bs, dest, node_positions,
                                         scenario.physics.travel_speed_kmh)
        bs.current_time = arrival
        bs.position     = dest
        bs.done         = True

    return seq


def _start_charging(
    bus_id:      str,
    station_id:  str,
    start_time:  float,
    charger:     ChargerState,
    context:     ScheduleContext,
    event_queue: List[Event],
    seq:         int,
    scenario:    Scenario,
) -> int:
    """
    Begin a charging session for one bus on one charger.

    Records the ChargingEvent, updates charger availability, appends to station log,
    and pushes a CHARGING_COMPLETE event.

    After charging: current_range_km is reset to battery_range_km (full charge assumed).
    wait_time is computed as start_time − station_arrival_time (may be 0 if charger was free).
    """
    bs: BusState     = context.bus_states[bus_id]
    ss: StationState = context.station_states[station_id]
    charge_duration: int   = scenario.physics.charge_time_minutes
    charge_end:      float = start_time + charge_duration
    wait_time:       float = start_time - bs.station_arrival_time

    ce = ChargingEvent(
        station_id   = station_id,
        arrival_time = bs.station_arrival_time,
        wait_time    = wait_time,
        charge_start = start_time,
        charge_end   = charge_end,
    )
    bs.completed_events.append(ce)
    bs.total_wait_time  += wait_time
    bs.current_range_km  = scenario.physics.battery_range_km   # full charge restored

    charger.free_at = charge_end
    ss.charge_log.append({
        "bus_id":       bus_id,
        "operator":     bs.bus.operator,
        "arrival_time": bs.station_arrival_time,
        "wait_time":    wait_time,
        "charge_start": start_time,
        "charge_end":   charge_end,
        "charger_id":   charger.charger_id,
    })

    heapq.heappush(event_queue, Event(charge_end, seq, EventType.CHARGING_COMPLETE, bus_id, station_id))
    return seq + 1


def _compute_arrival_time(
    bus_state:      BusState,
    target_node:    str,
    node_positions: Dict[str, Dict[str, float]],
    speed_kmh:      float,
) -> float:
    """
    Compute arrival time at target_node from bus's current position and time.

    Uses pre-computed position maps — no segment traversal.  [R-03 fix]
    Handles both BK and KB directions correctly via direction-indexed positions.
    Values > 1440 represent next-day arrivals (displayed with '+1d' prefix by minutes_to_time_str).  [R-01 fix]

    Asserts that distance is non-negative — backward travel indicates a bug in plan assignment.
    """
    positions: Dict[str, float] = node_positions[bus_state.bus.direction]
    distance: float = positions[target_node] - positions[bus_state.position]
    assert distance >= 0.0, (
        f"Bus {bus_state.bus.id}: backward travel {bus_state.position} → {target_node}. "
        f"Check charging plan assignment."
    )
    travel_time: float = (distance / speed_kmh) * 60.0
    return bus_state.current_time + travel_time


def _build_result(
    scenario:        Scenario,
    bus_states:      Dict[str, BusState],
    station_states:  Dict[str, StationState],
) -> SimulationResult:
    """
    Build the complete SimulationResult from final bus and station states.

    Computes aggregate metrics:
      - total_network_wait_minutes:  sum of all bus wait times
      - per_operator_avg_wait:       average wait per operator
      - simulation_duration_minutes: last arrival minus first departure
      - max_single_bus_wait_minutes: worst-case individual total wait
    """
    timetables: List[BusTimetable] = _build_timetables(bus_states, scenario)
    logs:       List[StationChargeLog] = _build_station_logs(station_states)

    all_waits: List[float]              = [t.total_wait_time for t in timetables]
    op_ids:    List[str]                = list({t.operator for t in timetables})
    per_op:    Dict[str, float]         = {}
    for op in op_ids:
        waits = [t.total_wait_time for t in timetables if t.operator == op]
        per_op[op] = sum(waits) / len(waits) if waits else 0.0

    departures = [t.departure_time for t in timetables]
    arrivals   = [t.arrival_time   for t in timetables]

    return SimulationResult(
        scenario_id                  = scenario.meta.get("id", "unknown"),
        bus_timetables               = timetables,
        station_logs                 = logs,
        total_network_wait_minutes   = sum(all_waits),
        per_operator_avg_wait        = per_op,
        simulation_duration_minutes  = max(arrivals) - min(departures) if arrivals else 0.0,
        max_single_bus_wait_minutes  = max(all_waits) if all_waits else 0.0,
    )


def _build_timetables(
    bus_states: Dict[str, BusState],
    scenario:   Scenario,
) -> List[BusTimetable]:
    """Build one BusTimetable per bus, sorted by (direction, departure_time)."""
    result: List[BusTimetable] = []
    for bus_id, bs in bus_states.items():
        dep: float = time_str_to_minutes(bs.bus.departure)
        result.append(BusTimetable(
            bus_id          = bus_id,
            operator        = bs.bus.operator,
            direction       = bs.bus.direction,
            departure_time  = dep,
            charging_plan   = bs.charging_plan,
            charging_events = list(bs.completed_events),
            total_wait_time = bs.total_wait_time,
            arrival_time    = bs.current_time,
            total_trip_time = bs.current_time - dep,
        ))
    return sorted(result, key=lambda t: (t.direction, t.departure_time))


def _build_station_logs(
    station_states: Dict[str, StationState],
) -> List[StationChargeLog]:
    """Build one StationChargeLog per station, with entries sorted by charge_start time."""
    return [
        StationChargeLog(
            station_id = sid,
            entries    = sorted(ss.charge_log, key=lambda e: e["charge_start"]),
        )
        for sid, ss in station_states.items()
    ]
```

---

## 10. Streamlit App — `app.py`

```python
"""
app.py
=======
Streamlit entry point. Loads scenarios, runs simulation, renders three tab views.

The app layer should be thin: no business logic here.
Scenario loading, simulation, and UI rendering are all delegated to their modules.

Tab structure:
  Tab 1 — Scenario Input:  route, stations, buses, weights (scenario_view.py)
  Tab 2 — Bus Timetables:  per-bus charging events and arrival times (bus_timetable.py)
  Tab 3 — Station View:    per-station charging order and wait times (station_view.py)
"""

from __future__ import annotations

import streamlit as st
from pathlib import Path
from typing import List

from scheduler.loader import list_scenarios, load_scenario
from scheduler.engine import run_simulation
from scheduler.models import Scenario, SimulationResult

st.set_page_config(page_title="Bus Charging Scheduler", layout="wide")
st.title("🚌 Bus Charging Scheduler")

scenario_files: List[Path] = list_scenarios("scenarios")
scenario_names: List[str]  = [f.stem.replace("_", " ").title() for f in scenario_files]

selected_index: int = st.selectbox(
    "Select Scenario",
    range(len(scenario_names)),
    format_func=lambda i: scenario_names[i],
)

scenario: Scenario = load_scenario(str(scenario_files[selected_index]))


@st.cache_data
def cached_simulation(path: str) -> SimulationResult:
    """Cache simulation result by scenario file path. Reruns only when YAML changes."""
    s = load_scenario(path)
    return run_simulation(s)


result: SimulationResult = cached_simulation(str(scenario_files[selected_index]))

tab1, tab2, tab3 = st.tabs(["📋 Scenario Input", "🚌 Bus Timetables", "⚡ Station View"])

with tab1:
    from ui.scenario_view import render_scenario_view
    render_scenario_view(scenario)

with tab2:
    from ui.bus_timetable import render_bus_timetables
    render_bus_timetables(result.bus_timetables, scenario)

with tab3:
    from ui.station_view import render_station_view
    render_station_view(result.station_logs, scenario)
```

---

## 11. requirements.txt

```
streamlit>=1.35.0
pyyaml>=6.0
pandas>=2.0.0
```

---

## 12. Valid Charging Plans Reference

**BK (Bengaluru→Kochi): node positions A=100, B=220, C=320, D=440, Kochi=540**

| Plan | Gap 0→s1 | Gap s1→s2 | Gap s2→540 | Valid? |
|---|---|---|---|---|
| (A, C) | 100 | 220 | 220 | ✅ |
| (B, C) | 220 | 100 | 220 | ✅ |
| (B, D) | 220 | 220 | 100 | ✅ |
| (A, B) | 100 | 120 | 320 | ❌ 320>240 |
| (A, D) | 100 | 340 | — | ❌ 340>240 |
| (C, D) | 320 | — | — | ❌ 320>240 |

**Minimum valid 2-stop plans for BK:** `(A,C)`, `(B,C)`, `(B,D)`
Round-robin: bus-BK-01→(A,C), bus-BK-02→(B,C), bus-BK-03→(B,D), bus-BK-04→(A,C), …

**KB (Kochi→Bengaluru): positions from Kochi: D=100, C=220, B=320, A=440**
Minimum valid 2-stop plans for KB: `(D,B)`, `(C,B)`, `(C,A)`

---

## 13. Implementation Checklist (for Coding Agent)

### Core models and utilities
- [ ] `minutes_to_time_str` handles `m > 1440` with `+Nd` prefix
- [ ] `driver_shift_end_minutes()` handles midnight-crossing shifts
- [ ] `BusState` has `station_arrival_time: float = 0.0` and `current_plan_index: int = 0`
- [ ] `ChargerState` has `can_charge_at(t: float) → bool` that uses `time_of_day`
- [ ] `StationState.get_free_charger_at()` calls `can_charge_at()`, not raw `free_at`
- [ ] `ScheduleContext` has `time_of_day` property and `set_priority_override()`
- [ ] `ScheduleContext.get_remaining_distance()` uses `scenario.route.*` — no import from planner
- [ ] `Scenario.route` is `RouteProvider`, not `List[RouteSegment]`
- [ ] `SimulationResult` dataclass with 7 fields; engine returns it

### RouteProvider
- [ ] `RouteProvider` ABC defined in `models.py` with 5 abstract methods + 2 abstract properties
- [ ] `LinearRouteProvider` in `scheduler/routes/linear.py` implements all 7
- [ ] `LinearRouteProvider._compute_bk_positions()` pre-computes at construction, not per-call
- [ ] `get_next_reachable_stations()` returns stations sorted by distance (nearest first)

### Loader
- [ ] `load_scenario(path, world_dir)` reads scenario YAML and resolves `world_id`
- [ ] `_validate_scenario()` checks required keys and valid directions
- [ ] `_validate_world()` checks required world keys
- [ ] Loader selects `LinearRouteProvider` or raises `NotImplementedError` based on `route.type`
- [ ] `_build_charger_state()` converts available_from/until strings to float minutes

### Planner
- [ ] `get_valid_charging_plans()` uses `scenario.route.get_node_positions()`, not raw segments
- [ ] `select_charging_plan()` uses `bus_index % len(candidates)` for round-robin

### Engine
- [ ] `Event` has `sequence: int` as second sort field
- [ ] `ScheduleContext` created once before the event loop
- [ ] `_compute_arrival_time()` uses pre-computed `node_positions` dict
- [ ] `current_range_km` decremented after each travel segment and final leg
- [ ] `_build_result()` computes all 7 `SimulationResult` fields

### Rules
- [ ] `IndividualWaitRule`, `OperatorFairnessRule`, `OverallThroughputRule` implemented
- [ ] `DriverShiftProximityRule` implemented with urgency_ratio logic
- [ ] `WeightedScorer.score()` applies `priority_overrides` from context

### YAML files
- [ ] `world/bengaluru_kochi.yaml` contains route + physics + stations
- [ ] `scenarios/scenario_1.yaml` contains `meta.world_id`, operators, weights, buses
- [ ] All 5 scenario files reference `world_id: "bengaluru_kochi"`

### Known V1 limitations (do NOT implement — document and own)
- [ ] Static charging plans (R-16) — documented; V2 fix is JIT routing via `get_next_reachable_stations()`
- [ ] Driver shift hard rule not wired into planner (data model ready)
- [ ] `charge_to_full: false` branch not implemented in engine (flag present)
- [ ] Graph-based routing not implemented (LinearRouteProvider only)
- [ ] World/scenario split in loader only — UI still shows merged Scenario object