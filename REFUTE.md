# REFUTE.md — Issues, Gaps, and Architecture Risks (v2)

This file documents everything that is wrong, missing, or fragile in the current design.
Each entry notes: severity, the exact problem, the fix, and whether a core rewrite is required.

Cross-references: [FC-XX] = FUTURE_CHANGES.md scenario. [PLAN §N] = PLAN.md section.

---

## Severity Key
- 🔴 **Critical** — causes wrong output, crashes, or makes a named future change impossible
- 🟡 **Medium** — causes subtle bugs, misleading results, or a significant design smell
- 🟢 **Low** — missing convenience, minor inconsistency, or clean-code smell

---

## — ORIGINAL ISSUES (R-01 to R-15) — Status: All Fixed in PLAN.md v2 —

## R-01 🔴 Inter-Day Time Overflow
**Status: FIXED** — `minutes_to_time_str()` now handles `m > 1440` with `+Nd` prefix.

## R-02 🔴 `BusState` Missing `station_arrival_time` Field
**Status: FIXED** — Field added with `default=0.0`.

## R-03 🔴 `_compute_arrival_time` — KB Direction Fragile
**Status: FIXED** — Pre-computed node positions; no segment traversal.

## R-04 🟡 `current_range_km` Never Decremented
**Status: FIXED** — Decremented after every travel segment in engine.

## R-05 🟡 `plan.index(station_id)` Fragile
**Status: FIXED** — `current_plan_index: int` tracked explicitly in `BusState`.

## R-06 🟡 Event Tie Non-Determinism
**Status: FIXED** — `Event.sequence` monotonic secondary sort key added.

## R-07 🟡 Soft Rule Score Scales Mismatched
**Status: DOCUMENTED** — Range differences noted in `WeightedScorer` docstring and `OVERVIEW.md §8`.

## R-08 🟡 Circular Import Risk (`ScheduleContext` in `engine.py`)
**Status: FIXED** — `ScheduleContext` moved to `models.py`.

## R-09 🟡 `ScheduleContext` Recreated Every Event
**Status: FIXED** — Created once before the event loop; `current_time` updated in-place.

## R-10 🟡 All Buses Get the Same Charging Plan
**Status: FIXED** — Round-robin across valid minimum-stop plans via `bus_index % len(candidates)`.

## R-11 🟢 Rules Hardcoded in `app.py`
**Status: DOCUMENTED** — V2 item. A `RuleRegistry` loaded from YAML is the clean fix.

## R-12 🟢 Hard Rules Defined but Never Called
**Status: DOCUMENTED** — V2 item. `planner._is_valid_plan()` should delegate to `RangeConstraint.is_satisfied()`.

## R-13 🟢 Driver Shift Midnight Crossing
**Status: FIXED** — `driver_shift_end_minutes()` helper handles midnight-crossing shifts.

## R-14 🟢 No Input Validation in `loader.py`
**Status: FIXED** — `_validate_scenario()` raises `ValueError` with helpful message on malformed input.

## R-15 🟢 Untyped Return from `get_station_positions`
**Status: FIXED** — `RoutePositions` named tuple added.

---

## — NEW ISSUES (R-16 to R-25) —

---

## R-16 🔴 Static Charging Plans Cannot Handle Dynamic Charger Failures

**Problem:** Each bus receives a `charging_plan: List[str]` before the simulation begins
(`select_charging_plan()` in `planner.py`). This static list is never revised. If Station C's
charger fails at 21:30 — while bus-BK-03 is en route from B to C — the bus arrives at C,
finds no charger, and the event loop has no handler to recover.

The code as written will either stall (bus stuck in a waiting queue that never resolves)
or silently skip the charge and produce a schedule that violates the 240km battery constraint.

**Concrete failure:** Scenario 5 (Worst Case) has high charger contention. Adding a single
`operational: false` charger at C during a run breaks every BK bus that planned to stop at C.

**Fix (requires moderate engine change):**
Replace static pre-assignment with a JIT (Just-In-Time) query. When a bus finishes charging
and prepares to travel, it queries `RouteProvider.get_next_reachable_stations(from, direction, range_km)`
against the *live* `station_states` to pick its next stop.

```python
# In _handle_charge_complete(), replace static plan lookup with:
live_stations = [
    sid for sid in
    scenario.route.get_next_reachable_stations(
        bs.position, bs.bus.direction, bs.current_range_km
    )
    if context.station_states[sid].has_operational_charger()
]
next_station = live_stations[0]  # or scored selection
```

This requires `RouteProvider.get_next_reachable_stations()` — see R-17. The `charging_plan`
field on `BusState` then becomes a *log* of where the bus has been (for UI display) rather
than a prescription of where it will go.

**Does this require a core rewrite?** Yes — engine event flow changes. Scope: ~50 lines across
`engine.py` and `planner.py`. The data model (`BusState.charging_plan` repurposed as a log)
and rule interfaces are unchanged.

**Note:** V1 is correct for scenarios with no charger failures. Flag this as a known limitation.

---

## R-17 🔴 Route is Hardcoded as a Linear Segment List — No RouteProvider Abstraction

**Problem:** `Scenario.segments: List[RouteSegment]` forces a single linear path. The planner,
engine, and `ScheduleContext` all iterate or index this list directly. The consequence:

1. Adding a second route (Bengaluru–Mysuru) requires schema changes to `Scenario`, new
   position-computation logic in `planner.py`, and conditional direction logic in the engine.
2. Supporting a graph topology (multiple valid paths between two nodes) is impossible without
   a rewrite — there is nowhere to plug it in.
3. `planner.get_all_node_positions()` is a free function that any file can call, bypassing any
   future abstraction layer.

**Fix:** Introduce a `RouteProvider` abstract base class in `scheduler/routes/base.py`.

```
RouteProvider (ABC)
  ├── get_node_positions(direction) → Dict[str, float]
  ├── get_station_ids()             → List[str]
  ├── get_total_distance()          → float
  ├── get_next_reachable_stations(from, direction, range_km) → List[str]
  ├── origin                        (property)
  └── destination                   (property)

LinearRouteProvider(RouteProvider)  ← V1 implementation
GraphRouteProvider(RouteProvider)   ← V2, NetworkX-based
```

Replace `Scenario.segments` with `Scenario.route: RouteProvider`.
`ScheduleContext.get_remaining_distance()` and the planner call `self.scenario.route.*`
instead of importing and calling `get_all_node_positions()`.

**Does this require a core rewrite?** Moderate refactor. Every call site that touches `scenario.segments`
or calls `get_all_node_positions()` updates to use `scenario.route.*`. Scope: ~6 call sites,
~30 lines. No logic changes — only the access pattern.

---

## R-18 🟡 World Data and Scenario Data Are Conflated in One YAML File

**Problem:** Route topology, station hardware, and physics constants belong to the *physical
world* — they change once a year (new charger installed, road realignment). Bus schedules,
operator weights, and rule weights belong to the *scenario* — they change every run.
Mixing both in `scenario_1.yaml` means:

- Comparing two scheduling strategies requires duplicating 80% of the YAML (world section)
  in every scenario file.
- An operations team editing bus departure times must edit the same file as an infra team
  changing charger counts. Merge conflicts guaranteed.
- Bulk "what-if" runs (e.g., test 50 weight combinations against one network) require 50
  near-identical YAML files.

**Fix:** Split into two file types.

```
world/
└── bengaluru_kochi.yaml    # route segments, station hardware, physics
scenarios/
└── scenario_1.yaml         # buses, weights, operators + "world: bengaluru_kochi"
```

Loader: `load_scenario(path)` reads the scenario, resolves `world:` reference, loads
`world/<id>.yaml`, and assembles the combined `Scenario` object. The engine never sees the split.

**Does this require a core rewrite?** No. Loader change only (~30 lines). Engine and models unchanged.

---

## R-19 🟡 `ScheduleContext.get_remaining_distance()` Violates Layer Boundaries

**Problem:** `ScheduleContext` lives in `models.py`. Its `get_remaining_distance()` method
contains this runtime import:

```python
from scheduler.planner import get_all_node_positions  # ← inside a method body
```

This is a deferred circular import (models → planner) disguised as a local import.
It also means every call to `get_remaining_distance()` re-imports and re-computes all node
positions from scratch — O(segments) work done O(queue_size × buses) times per run.

The clean dependency graph is: `models` has zero scheduler imports. Planner and engine
import from models, not the reverse.

**Fix (free once R-17 is done):** With `RouteProvider` on `Scenario`, the method becomes:

```python
def get_remaining_distance(self, bus_state: BusState) -> float:
    positions = self.scenario.route.get_node_positions(bus_state.bus.direction)
    dest = (
        self.scenario.destination if bus_state.bus.direction == "BK"
        else self.scenario.origin
    )
    return positions[dest] - positions[bus_state.position]
```

No import from planner. Position data is pre-computed once in `RouteProvider.__init__()`.

**Does this require a core rewrite?** No. 5-line change, contingent on R-17.

---

## R-20 🟢 `charge_to_full` Is a Global Physics Flag, Not Per-Bus

**Problem:** `Physics.charge_to_full: bool` is a single global switch. In real operation,
buses may have different charge strategies:

- A bus near its destination only needs a partial top-up (saves ~10 minutes).
- A bus with a premium SLA or long remaining journey always charges to full.
- A driver approaching shift end needs the minimum charge to reach the next station.

With the current design, all buses on all runs have the same strategy, or you need a separate
scenario file per strategy combination.

**Fix:** Add optional `charge_strategy: str` field to `Bus` with values `"full"` (default)
and `"required"`. Engine reads `bus.charge_strategy` (falling back to `physics.charge_to_full`)
in `_start_charging()`.

```yaml
buses:
  - id: "bus-BK-07"
    charge_strategy: "required"   # top-up only
```

**Does this require a core rewrite?** No. One optional field on `Bus`, ~15 lines in `_start_charging()`.

---

## R-21 🟢 Engine Returns a Bare Tuple — No `SimulationResult` Wrapper

**Problem:** `run_simulation()` currently returns `Tuple[List[BusTimetable], List[StationChargeLog]]`.
Every call site destructures this tuple with positional indexing. Adding a third return value
(e.g., aggregate metrics, simulation warnings, run metadata) breaks every call site.

```python
# Current — brittle:
timetables, logs = run_simulation(scenario)

# Future — if aggregate metrics are added, this breaks:
timetables, logs, metrics = run_simulation(scenario)  # ← IndexError at all old call sites
```

**Fix:** Introduce a `SimulationResult` frozen dataclass:

```python
@dataclass(frozen=True)
class SimulationResult:
    scenario_id: str
    bus_timetables: List[BusTimetable]
    station_logs: List[StationChargeLog]
    total_network_wait_minutes: float
    per_operator_avg_wait: Dict[str, float]
    simulation_duration_minutes: float
```

Engine returns `SimulationResult`. New fields with defaults can be added without breaking callers.

**Does this require a core rewrite?** No. 1 new dataclass, update 2 call sites.

---

## R-22 🟢 No Time-of-Day Helper on `ScheduleContext`

**Problem:** Rules that care about time of day (electricity tariff, charger maintenance windows,
route availability) must manually compute `context.current_time % 1440`. This magic number is
scattered across multiple rule files with no encapsulation, making it easy to forget for
multi-day simulations.

**Fix:** Add a property to `ScheduleContext`:

```python
@property
def time_of_day(self) -> float:
    """Current simulation time normalised to [0, 1440) for time-of-day rule evaluation."""
    return self.current_time % MINUTES_PER_DAY
```

Rules then write `context.time_of_day` instead of inline modular arithmetic.

**Does this require a core rewrite?** No. 3-line property addition.

---

## R-23 🟢 `DriverShiftProximityRule` Missing — Shift Urgency Is Binary, Not Graduated

**Problem:** The schema captures driver shift data. The architecture notes a `ShiftUrgencyRule`
in `FUTURE_CHANGES.md`. But as designed, shift urgency is either "not violated" (rule passes)
or "violated" (hard constraint fires). There is no graduated scoring: a bus 5 hours from
shift end and a bus 20 minutes from shift end are treated identically in the soft rule layer.

**Fix:** Add `DriverShiftProximityRule(SoftRule)`:

```python
class DriverShiftProximityRule(SoftRule):
    """
    Score increases non-linearly as remaining time approaches estimated remaining journey.
    When shift_remaining < journey_remaining * 1.5, urgency activates.
    
    Natural range: [0, ~300] — low until the crunch zone, then spikes sharply.
    """
    name: str = "shift_proximity"

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        if not bus_state.bus.driver_shift:
            return 0.0
        shift_end        = driver_shift_end_minutes(bus_state.bus.driver_shift)
        shift_remaining  = shift_end - context.current_time
        journey_remaining_min = (
            context.get_remaining_distance(bus_state)
            / context.scenario.physics.travel_speed_kmh
        ) * 60.0
        if shift_remaining <= 0 or journey_remaining_min <= 0:
            return 300.0   # maximum urgency (shift already tight or exceeded)
        urgency_ratio = journey_remaining_min / shift_remaining
        # Score is 0 below threshold, rises sharply above it
        return max(0.0, (urgency_ratio - 0.5) * 200.0)
```

**Does this require a core rewrite?** No. New SoftRule class + 1 registration line.

---

## R-24 🟡 No Support for Multiple Concurrent Routes Sharing Stations

**Problem:** The current data model assumes one route (Bengaluru→Kochi). Station states
are keyed by station ID with no concept of which route a bus is on. Adding a second route
(e.g., Bengaluru→Mysuru stopping at A and B) requires:

1. Disambiguating bus direction — "BK" only describes one route; routes need IDs.
2. The planner needs per-route position maps, not just per-direction.
3. `OverallThroughputRule` uses "remaining distance to destination" — but destination depends
   on the route, not just direction.

There is currently no `route_id` on `Bus`, no multi-route `Scenario` schema, and no planner
support for heterogeneous route topologies.

**Fix for V1.5 (minimal):** Add `route_id: str = "default"` to `Bus` and `RouteProvider`.
The engine dispatches to the correct `RouteProvider` via `scenario.get_route(bus.route_id)`.

**Fix for V2 (full):** `Scenario` holds `Dict[str, RouteProvider]`. Planner and engine look up
by `bus.route_id`. `ScheduleContext.get_remaining_distance()` uses the bus's route provider.

**Does this require a core rewrite?** Moderate. Data model changes to `Bus` and `Scenario`,
planner and engine updated to index by route_id. No logic changes, only dispatch changes.

---

## R-25 🟢 `ChargerState` Missing `can_charge_at()` Helper

**Problem:** Charger availability logic (`operational`, `available_from`, `available_until`,
`free_at`) is currently spread across the loader (which filters out non-operational chargers)
and `StationState.get_free_charger_at()` (which checks only `free_at`). Charger maintenance
windows (`available_from`/`available_until`) are parsed in the YAML but silently ignored
during simulation — the loader includes them in the data model but the engine never reads them.

This means scenario YAML expressing `available_from: "06:00", available_until: "22:00"` has
no effect on the simulation. A bus arriving at 23:00 will be assigned a charger that should
be in maintenance.

**Fix:** Add `can_charge_at(t: float) -> bool` to `ChargerState`:

```python
@dataclass
class ChargerState:
    charger_id:      str
    available_from:  float = 0.0      # pre-converted to minutes at load time
    available_until: float = 1440.0   # pre-converted to minutes at load time
    is_operational:  bool  = True
    free_at:         float = 0.0

    def can_charge_at(self, t: float) -> bool:
        """True if this charger is operational, within its availability window, and free."""
        day_t = t % 1440.0
        return (
            self.is_operational
            and self.available_from <= day_t <= self.available_until
            and self.free_at <= t
        )
```

Update `StationState.get_free_charger_at()` to call `can_charge_at()` instead of `is_free_at()`.

**Does this require a core rewrite?** No. Add method to dataclass, update 1 call site.

---

## Summary: All Issues

| Issue | Severity | Core Change? | Effort |
|---|---|---|---|
| R-01 Inter-day time | 🔴 | No | 5 lines |
| R-02 Missing BusState field | 🔴 | No | 2 lines |
| R-03 Fragile arrival time | 🔴 | No — refactor | 20 lines |
| R-04 Range not decremented | 🟡 | No | 1 line |
| R-05 `plan.index` fragility | 🟡 | No | 2 fields |
| R-06 Event non-determinism | 🟡 | No | 1 field + counter |
| R-07 Score scale mismatch | 🟡 | No — document | 0 lines |
| R-08 Circular import | 🟡 | No — move class | 5 min |
| R-09 Context per-event | 🟡 | No | 2-line hoist |
| R-10 All buses same plan | 🟡 | No | 3 lines |
| R-11 Rules hardcoded | 🟢 | No — V2 registry | Moderate |
| R-12 Hard rules never called | 🟢 | No | 3 lines |
| R-13 Driver shift midnight | 🟢 | No | 8 lines |
| R-14 No input validation | 🟢 | No | 20 lines |
| R-15 Untyped return | 🟢 | No | 5 lines |
| **R-16 Static plan + dynamic failures** | 🔴 | **Yes — moderate** | ~50 lines |
| **R-17 No RouteProvider abstraction** | 🔴 | **Yes — moderate refactor** | ~30 lines |
| R-18 World/scenario conflation | 🟡 | No — loader only | ~30 lines |
| R-19 Layer violation in context | 🟡 | No — free after R-17 | 5 lines |
| R-20 Global charge_to_full | 🟢 | No | 15 lines |
| R-21 No SimulationResult | 🟢 | No | 1 dataclass |
| R-22 No time-of-day helper | 🟢 | No | 3 lines |
| R-23 Shift proximity rule missing | 🟢 | No | 1 new SoftRule |
| R-24 No multi-route support | 🟡 | Yes — moderate | ~40 lines |
| R-25 ChargerState missing helper | 🟢 | No | 10 lines |

**Items requiring core changes: R-16, R-17, R-24. All others are targeted fixes.**

---

## R-26 🟡 Direction-Only Buses Cannot Represent Intermediate Trips

**Status: FIXED IN IMPLEMENTATION** — The implemented model supports explicit `origin_node`
and `destination_node` on each bus while preserving `direction: "BK"|"KB"` compatibility for
the original full-corridor scenarios.

**Problem:** A direction-only bus model can represent Bengaluru→Kochi and Kochi→Bengaluru,
but not operationally common short legs like A→B or B→A. Those trips may still contend for
chargers if they need depot/top-up charging before departure.

**Fix implemented:** Loader derives direction from the endpoint positions when explicit nodes
are provided. The planner generates charging plans only between the bus's endpoints. Short
trips such as A→B usually have no en-route charging plan, and `requires_origin_charge: true`
models pre-departure charger contention at the origin station.

**Remaining limitation:** This is still a single linear-route model. Multi-route shared
stations remain R-24/FC-22 future work.
