# FUTURE_CHANGES.md — Anticipated Changes & Design Resilience (v2)

For each change: data impact, code impact, class/function references, and whether the engine needs rewrites.

Cross-references: [R-XX] = REFUTE.md issue.

---

## Category 1 — Physical World Changes

### FC-01 — Battery Range Per Bus Type
**Data change:** `buses[].battery_range_km` (optional per-bus override).
**Code touch:** `planner.select_charging_plan()` reads `bus.battery_range_km` if set, else `scenario.physics.battery_range_km`. ~3 lines.
**Engine rewrite:** No.

---

### FC-02 — Charging Time Per Charger
**Data change:** `stations[].chargers[].charge_time_minutes` (optional per-charger override).
**Code touch:** `engine._start_charging()` reads `charger.charge_time_minutes` if set, else `scenario.physics.charge_time_minutes`. ~3 lines.
**Engine rewrite:** No.

---

### FC-03 — Partial Charging (Charge to Required Level)
**Data change:** `physics.charge_to_full: false` (already in schema).
Per-bus override: `buses[].charge_strategy: "required"` (see R-20).
**Code touch:** ~15 lines in `engine._start_charging()`. No schema change.
**Engine rewrite:** No.

---

### FC-04 — Variable Speed Per Segment
**Data change:** `route.segments[].speed_kmh_override: 40`
**Code touch:** `engine._compute_arrival_time()` reads `speed_kmh_override` if set. ~5 lines.
**Engine rewrite:** No.

---

## Category 2 — Infrastructure Changes

### FC-05 — Add Second Charger to a Station
**Data change:** Add second entry to `stations[].chargers`.
**Code touch:** None. `StationState.charger_states` is already `List[ChargerState]`.
**Engine rewrite:** No.

---

### FC-06 — Dynamic Charger Failure / Maintenance Window
**Data change:** `stations[].chargers[].operational: false` or `available_until: "22:00"`.
**Code touch:** `ChargerState.can_charge_at(t)` helper (R-25 fix). `StationState.get_free_charger_at()` calls it.
Optionally, a `CHARGER_FAILED` event type that fires mid-simulation.
**Engine rewrite:** No.

---

### FC-07 — Add a New Intermediate Station
**Data change:** Two new segment rows + one station entry.
**Code touch:** None. `RouteProvider` rebuilds positions automatically.
**Engine rewrite:** No.

---

### FC-08 — Remove or Decommission a Station
**Data change:** Set all chargers `operational: false`.
**Code touch:** Planner skips stations with no active chargers. ~2 lines.
**Engine rewrite:** No.

---

### FC-09 — Multiple Routes Sharing Stations
**Data change:** `world/` file gets a `routes:` list; `buses[].route_id` references one.
See FC-22 (Graph-Based Topology) for full treatment.
**Code touch:** Moderate loader + planner + engine update. No logic rewrite.
**Engine rewrite:** No.

---

## Category 3 — Operator & Bus Changes

### FC-10 — Add a New Operator
**Data change:** One row in `operators`, referenced from `buses`.
**Code touch:** None.
**Engine rewrite:** No.

---

### FC-11 — Per-Operator SLA Weight
**Data change:** `operators[].weight: 2.0`
**Code touch:** None. `OperatorFairnessRule.score()` already reads `op_config.weight`.
**Engine rewrite:** No.

---

### FC-12 — Per-Bus Priority Override
**Data change:** `buses[].weight: 5.0` and/or `priority_class: "priority"`.
**Code touch:** None. `WeightedScorer.score()` multiplies by `bus.weight`.
**Engine rewrite:** No.

---

### FC-13 — Driver Shift Constraints
**Data change:** `buses[].driver_shift.start/end` (already in schema).
**Code touch:** `DriverShiftProximityRule(SoftRule)` (R-23 fix). `DriverShiftHardRule(HardRule)` for plan-time enforcement.
**Engine rewrite:** No.

---

## Category 4 — Economic & Operational Rules

### FC-14 — Electricity Cost by Time of Day
**Data change:**
```yaml
physics:
  electricity:
    tariff_schedule:
      - {start: "00:00", end: "18:00", multiplier: 1.0}
      - {start: "18:00", end: "22:00", multiplier: 1.4}
      - {start: "22:00", end: "23:59", multiplier: 1.0}
```
**Code touch:** `ElectricityCostRule(SoftRule)`. Uses `context.time_of_day` (R-22 fix).
Add `electricity: float = 1.0` to `Weights` dataclass with default.
**Engine rewrite:** No.

---

### FC-15 — Station Waiting Area Capacity Limit
**Data change:** `stations[].max_waiting: 3`
**Code touch:** `engine._handle_arrival()` checks queue length. Emits `BUS_DIVERTED` event if full.
Planner re-runs `get_next_reachable_stations()` excluding the full station.
**Engine rewrite:** No. ~20 lines.

---

### FC-16 — Emergency Bus Preemption
**Data change:** `buses[].weight: 999, priority_class: "emergency"`
**Code touch (zero for soft preemption):** Weight=999 wins the scorer automatically.
For true hard preemption (interrupt ongoing charge): new `PREEMPT_CHARGE` event type + handler.
**Engine rewrite:** No (soft). Isolated addition (hard).

---

## Category 5 — Algorithm Upgrades

### FC-17 — Congestion-Aware Plan Selection
**Code touch:** `planner.select_charging_plan()` accepts optional `station_loads: Dict[str, int]`.
Candidates sorted by total queue depth across their stations.
**Engine rewrite:** No. ~10 lines.

---

### FC-18 — Scale to 500+ Buses
**Code touch:** Cache operator delay sums in `ScheduleContext` with incremental updates.
Turns O(B) per score call into O(1).
**Engine rewrite:** No. ~15 lines.

---

## Category 6 — Route & Topology Changes (NEW)

### FC-19 — Route Availability Windows (Service Hours)

**Scenario:** The Bengaluru–Kochi service only accepts new bus departures between 16:00 and 23:00.
No buses should depart after 23:00 or be expected to reach the route's first charging station
if it violates a route-level curfew. A separate Mysuru day-service route runs 06:00–20:00 only.

**Real-life parallel:** Many overnight sleeper bus services have hard cutoffs for boarding;
a bus departing past midnight on a route that serves office commuters is operationally invalid.

**Data change:**
```yaml
# world/bengaluru_kochi.yaml
route:
  id: "BK"
  service_window:
    earliest_departure: "16:00"
    latest_departure:   "23:30"
    active_days: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
```

**Code touch:** New `RouteAvailabilityHardRule(HardRule)` — validates departure times at load time.
Optionally, `engine._handle_arrival()` checks `service_window` for the route and emits an
`OUT_OF_SERVICE` event if a bus would arrive at a station outside the window.

**Classes:** `RouteProvider` (add `service_window` attribute), new `RouteAvailabilityHardRule`, `loader._validate_scenario()`.
**Engine rewrite:** No.

---

### FC-20 — Pick-Up / Drop-Off Stops (Not All Stops Are Charging Stops)

**Scenario:** Station B is both a charging station and a passenger boarding stop. The bus must
arrive at B by 20:30 for passenger boarding (a hard time constraint) regardless of charging status.
Some stops on the route are boarding-only — no charger present, but the bus schedule mandates a stop.

**Real-life parallel:** Intercity coaches stop at city centres for passengers; the bus waits a
fixed dwell time and then departs whether fully charged or not. A charging stop and a boarding
stop have completely different dwell-time logic.

**Data change:**
```yaml
stations:
  - id: "B"
    stop_type: "charging_and_boarding"   # or "charging_only", "boarding_only"
    boarding_dwell_minutes: 10           # mandatory dwell even if no charge needed
    boarding_deadline: "20:30"           # bus must arrive before this time
```

**Code touch:**
New `StopType` enum: `CHARGING_ONLY`, `BOARDING_ONLY`, `CHARGING_AND_BOARDING`.
New event type: `BUS_BOARDING_STOP`. For boarding stops, the engine schedules a fixed
`dwell_time` departure regardless of charging queue. Boarding deadline enforced as a
`ScheduledDepartureHardRule`.

**Classes:** `Station` (add `stop_type`, `boarding_dwell_minutes`, `boarding_deadline`),
new `EventType.BUS_BOARDING_STOP`, new `ScheduledDepartureHardRule`.
**Engine rewrite:** No — additive new event type.

---

### FC-21 — Mid-Journey Route Diversion

**Scenario:** A bus traveling BK reaches Station C and the operator calls an audible —
the bus will terminate at C rather than continuing to Kochi (partial route, different operator payment).
Alternatively, due to a road incident, buses are diverted from the primary route to an alternate
path after Station B.

**Real-life parallel:** Breakdown diversion, unexpected road closures, commercial schedule changes.

**Data change:**
```yaml
buses:
  - id: "bus-BK-05"
    diversion:
      trigger_station: "C"
      new_destination:  "C"     # terminates at C
      # or:
      new_route_id: "BK_ALT"   # switches to alternate route after C
```

Or a runtime event injected via API/YAML patch mid-simulation.

**Code touch:**
New `EventType.BUS_DIVERTED`. Handler calls `scenario.get_route(bus.diversion.new_route_id)`
and re-assigns the bus's `RouteProvider`. `BusState.route_id` becomes mutable (or a new
`BusState` is created for the diverted leg). The rest of the engine is unchanged — it just
dispatches events against the new route's topology.

**Classes:** `Bus` (add optional `diversion`), `BusState` (add `route_id`), new `EventType.BUS_DIVERTED`.
**Engine rewrite:** No — additive event type.

---

### FC-22 — Graph-Based Multi-Route Topology (NetworkX)

**Scenario:** The route network grows: Bengaluru–Kochi, Bengaluru–Mysuru, Mysuru–Kochi are three
routes that share some stations. A bus on the Bengaluru–Mysuru route stops at A. A bus on the
Bengaluru–Kochi route also stops at A. Station A manages both. Future: a bus originating in
Mysuru connects to Kochi via a transfer at B. The scheduler must find valid charging paths
through a full graph, not just a single line.

**Real-life parallel:** A national bus network where dozens of routes share depots and charging
hubs. Each route is a subgraph of the national road network.

**Implementation approach:**

```python
# scheduler/routes/graph.py

import networkx as nx

class GraphRouteProvider(RouteProvider):
    """
    NetworkX-backed multi-path route. Implements the same RouteProvider interface
    as LinearRouteProvider. The engine, planner, and rules are unaware of the change.

    Valid paths are all simple paths from origin to destination.
    get_next_reachable_stations() uses Dijkstra on the subgraph filtered by range_km.
    """
    def __init__(self, graph: nx.DiGraph, origin: str, destination: str, station_ids: List[str]):
        self._graph = graph
        self._origin = origin
        self._destination = destination
        self._station_ids = station_ids
        # Pre-compute shortest-path positions for default direction
        self._bk_positions = nx.single_source_dijkstra_path_length(
            graph, origin, weight="distance_km"
        )

    def get_next_reachable_stations(self, from_node: str, direction: str, range_km: float) -> List[str]:
        """
        Stations within range_km along any valid forward path.
        Enables graph routing: if Station C is unreachable within range via the direct path,
        the engine can try an alternate path through B2.
        """
        from_pos = self._bk_positions[from_node]
        return [
            sid for sid in self._station_ids
            if 0 < self._bk_positions.get(sid, -1) - from_pos <= range_km
        ]
```

**Data change:**
```yaml
route:
  type: "graph"
  edges:
    - {from: "Bengaluru", to: "A",  distance_km: 100}
    - {from: "A",         to: "B",  distance_km: 120}
    - {from: "B",         to: "C",  distance_km: 100}
    - {from: "B",         to: "B2", distance_km: 60}   # alternate path
    - {from: "B2",        to: "C",  distance_km: 60}
    - {from: "C",         to: "D",  distance_km: 120}
    - {from: "D",         to: "Kochi", distance_km: 100}
```

**Code touch:** New `GraphRouteProvider(RouteProvider)`. Loader selects `LinearRouteProvider`
or `GraphRouteProvider` based on `route.type`. Engine, planner, and rules need zero changes
because they only call `RouteProvider` interface methods.

**Classes:** New `scheduler/routes/graph.py`. `loader._parse_world()` dispatches on `route.type`.
**Engine rewrite:** No — this is the entire point of the RouteProvider pattern.

---

### FC-23 — Per-Bus Charge Strategy ("Required" vs "Full")

**Scenario:** See R-20. A bus 80km from its destination needs only 80km of charge (plus safety
buffer), not 240km. Charging to full wastes 10–15 minutes of charger time that another bus could use.

**Data change:** `buses[].charge_strategy: "required"` (see R-20 for schema).

**Code touch:**
```python
# engine._start_charging()
if bus.charge_strategy == "required":
    next_stop_dist = positions[next_station] - positions[current_station]
    buffer_km = 20.0
    km_needed = max(0, next_stop_dist + buffer_km - bs.current_range_km)
    fraction = km_needed / scenario.physics.battery_range_km
    charge_duration = int(fraction * scenario.physics.charge_time_minutes)
else:
    charge_duration = scenario.physics.charge_time_minutes  # full charge
```

**Classes:** `Bus` (add `charge_strategy` field), `engine._start_charging()`.
**Engine rewrite:** No. ~15 lines.

---

### FC-24 — Aggregate Simulation Metrics in `SimulationResult`

**Scenario:** The ops team wants dashboards showing: total network wait time, worst-case single
bus delay, per-operator fairness score, charger utilisation rate per station. Adding these to
the return type of `run_simulation()` today breaks every call site (see R-21).

**Fix:** `SimulationResult` wrapper (see R-21). Add computed metric fields:

```python
@dataclass(frozen=True)
class SimulationResult:
    scenario_id:                  str
    bus_timetables:               List[BusTimetable]
    station_logs:                 List[StationChargeLog]
    total_network_wait_minutes:   float
    per_operator_avg_wait:        Dict[str, float]
    simulation_duration_minutes:  float
    charger_utilisation:          Dict[str, float]  # station_id → fraction [0, 1]
    max_single_bus_wait_minutes:  float
```

**Code touch:** New `SimulationResult` dataclass. `engine._build_result()` computes metrics
from `bus_states` and `station_states` at end of simulation.
**Engine rewrite:** No. Additive.

---

### FC-25 — Dynamic Charger Failure + JIT Re-routing

**Scenario:** Station C's charger fails at 21:30, while bus-BK-03 is en route from B to C.
The engine must detect that bus-BK-03's planned stop is now non-functional and re-route it
to an alternate station (D, or back to B if within range).

**This is the full fix for R-16.**

**Implementation:**

```python
# New event type:
class EventType(Enum):
    BUS_ARRIVES_AT_STATION = auto()
    CHARGING_COMPLETE      = auto()
    CHARGER_FAILED         = auto()   # ← new: injected by ops or test scenario
```

```python
# In _handle_charger_failed():
def _handle_charger_failed(event, context, ...):
    ss = context.station_states[event.station_id]
    ss.mark_all_chargers_failed()   # sets operational=False, removes from free list
    # Re-route all buses en route to this station
    for bus_id, bs in context.bus_states.items():
        if bs.next_planned_station == event.station_id:
            # JIT: find next reachable station with an operational charger
            alternates = [
                sid for sid in scenario.route.get_next_reachable_stations(
                    bs.position, bs.bus.direction, bs.current_range_km
                )
                if context.station_states[sid].has_operational_charger()
            ]
            if alternates:
                bs.next_planned_station = alternates[0]  # re-assign
            else:
                bs.done = True; bs.stranded = True       # no valid path
```

**Data change:** `scenarios/scenario_N.yaml` can inject a charger failure event:
```yaml
events:
  - type: "charger_failed"
    station_id: "C"
    at_time: "21:30"
```

**Classes:** New `EventType.CHARGER_FAILED`, new `_handle_charger_failed()` handler,
`BusState.next_planned_station` (replaces static `charging_plan` list — see R-16),
`StationState.has_operational_charger()`.
**Engine rewrite:** Moderate (50 lines). Architecture unchanged.

---

### FC-26 — Headway Management (Minimum Bus Spacing on Same Route)

**Scenario:** The operator requires at least 10 minutes between consecutive same-direction buses
at every station. Bunching (two buses arriving within 2 minutes of each other) creates passenger
confusion and suboptimal charger utilisation. The scheduler should, where possible, delay a
faster bus at an earlier station to maintain headway.

**Real-life parallel:** London Bus network uses headway controllers at timing points.
All major transit systems enforce minimum headway as an operational constraint.

**Data change:**
```yaml
route:
  min_headway_minutes: 10   # minimum gap between same-direction buses at any station
```

**Code touch:**
New `HeadwayRule(SoftRule)` — scores a waiting bus lower if a same-direction bus is
less than `min_headway_minutes` ahead of it. Encourages the closer-together bus to wait.

```python
class HeadwayRule(SoftRule):
    """
    Penalise buses that would arrive at the next station less than min_headway behind
    the preceding bus in the same direction. Reduces bunching.
    """
    name = "headway"

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        # Find the last bus in same direction that departed this station
        same_dir_departures = [
            e["charge_end"]
            for e in context.station_states[bus_state.position].charge_log
            if context.bus_states[e["bus_id"]].bus.direction == bus_state.bus.direction
        ]
        if not same_dir_departures:
            return 0.0
        last_departure = max(same_dir_departures)
        gap = context.current_time - last_departure
        headway = context.scenario.route.min_headway_minutes
        # Negative score if we're too close behind the bus ahead
        return min(0.0, (gap - headway) * 2.0)
```

**Classes:** New `HeadwayRule(SoftRule)`. `RouteProvider` gets `min_headway_minutes` attribute.
**Engine rewrite:** No.

---

### FC-27 — Real-Time Priority Override (Operator API)

**Scenario:** During a live run, an operator calls in: "bus-BK-07 is carrying medical supplies,
give it highest priority at every remaining station." This should not require re-loading the
scenario or restarting the simulation.

**Real-life parallel:** Ambulance-priority signal pre-emption at intersections; dynamic SLA
escalation in fleet management systems.

**Data change:** No YAML change. This is a runtime mutation.

**Code touch:**
`ScheduleContext` gets a mutable `priority_overrides: Dict[str, float]` dict.
`WeightedScorer.score()` checks this dict and applies a multiplier when present.
A thin external API (or a Streamlit "override" button) calls `context.set_priority_override(bus_id, multiplier)`.

```python
# ScheduleContext addition:
priority_overrides: Dict[str, float] = field(default_factory=dict)

def set_priority_override(self, bus_id: str, multiplier: float) -> None:
    self.priority_overrides[bus_id] = multiplier

# WeightedScorer.score() — one extra line:
override = context.priority_overrides.get(bus_state.bus.id, 1.0)
return override * bus_state.bus.weight * rule_total
```

**Classes:** `ScheduleContext` (add `priority_overrides`), `WeightedScorer.score()`.
**Engine rewrite:** No. ~10 lines.

---

### FC-28 — World vs Scenario YAML File Separation (See R-18)

**Scenario:** The ops team edits bus timetables daily. The infra team changes charger counts
once a year. Keeping both in the same YAML creates unnecessary merge conflicts and forces
full scenario duplication when only weights change.

**Implementation:**

```
world/
└── bengaluru_kochi.yaml      # route segments, station hardware, physics
scenarios/
└── scenario_1.yaml           # buses, operators, weights + "world_id: bengaluru_kochi"
```

Loader:
```python
def load_scenario(scenario_path: str, world_dir: str = "world") -> Scenario:
    config = _load_scenario_config(scenario_path)
    world  = _load_world(f"{world_dir}/{config['world_id']}.yaml")
    return _merge(config, world)   # produces standard Scenario object
```

Multiple scenarios can reference the same world. Changing `weights.operator` in
`scenario_1.yaml` does not touch the world file, and vice versa.

**Classes:** `loader.py` split into `_load_scenario_config()`, `_load_world()`, `_merge()`.
**Engine rewrite:** No. Loader change only.

---

## Summary Table (All FCs)

| FC | Schema change? | New Rule class? | Engine handler? | Engine rewrite? |
|---|---|---|---|---|
| FC-01 Battery per bus | 1 optional field | No | No | No |
| FC-02 Charge time per charger | 1 optional field | No | ~3 lines | No |
| FC-03 Partial charging | Flag present | No | ~15 lines | No |
| FC-04 Variable speed | 1 optional field | No | ~5 lines | No |
| FC-05 Add 2nd charger | List entry | No | No | No |
| FC-06 Charger failure window | Fields present | 1 HardRule | ~5 lines | No |
| FC-07 New station | 2 rows + 1 station | No | No | No |
| FC-08 Remove station | operational: false | No | No | No |
| FC-09 Multi-route | routes dict | No | Loader refactor | No |
| FC-10 New operator | 1 list entry | No | No | No |
| FC-11 Operator SLA weight | 1 value | No | No | No |
| FC-12 Per-bus priority | 1 value | No | No | No |
| FC-13 Driver shift | Optional block | 1 Hard + 1 Soft | No | No |
| FC-14 Electricity cost | Add tariff section | 1 Soft | No | No |
| FC-15 Station capacity | 1 optional field | No | ~20 lines | No |
| FC-16 Emergency preemption | weight: 999 | No (optional Hard) | Optional | No |
| FC-17 Congestion-aware plans | No | No | ~10 lines | No |
| FC-18 500 buses | No | No | ~15 lines | No |
| FC-19 Route availability windows | Service window block | 1 HardRule | ~10 lines | No |
| FC-20 Pick-up/boarding stops | stop_type, dwell | No | 1 new event type | No |
| FC-21 Mid-journey diversion | diversion block | No | 1 new event type | No |
| FC-22 Graph-based multi-route | edges list | No | No (RouteProvider swap) | **No** |
| FC-23 Per-bus charge strategy | 1 field on Bus | No | ~15 lines | No |
| FC-24 Aggregate metrics | No | No | 1 new builder fn | No |
| FC-25 Dynamic charger failure | events block | No | 1 new event type | No |
| FC-26 Headway management | min_headway field | 1 Soft | No | No |
| FC-27 Real-time priority override | No | No | ~10 lines | No |
| FC-28 World/scenario file split | New file layout | No | Loader only | No |

---

### FC-29 — Station-Origin Top-Up Charging Before Short Legs

**Scenario:** Buses can operate short legs such as A→B or B→A. Even though the trip is shorter
than full battery range, a bus may arrive at the origin station with low state of charge and
need a top-up before departure. Multiple buses leaving A or B at the same time contend for the
same charger before entering service.

**Real-life parallel:** Depot-origin or station-origin electric coaches often perform a short
pre-service top-up before a scheduled departure, especially after a prior inbound trip or layover.

**Data change:** Already supported in v1 implementation:
```yaml
buses:
  - id: "bus-AB-01"
    origin_node: "A"
    destination_node: "B"
    departure: "19:00"
    requires_origin_charge: true
    initial_range_km: 40
```

**Code touch:** No further engine rewrite for v1. Long-term, replace the boolean with a richer
charging intent object containing target state-of-charge, minimum departure SOC, and scheduled
layover window.

**Engine rewrite:** No for boolean top-up. Partial-charge SOC modeling is a future enhancement.
