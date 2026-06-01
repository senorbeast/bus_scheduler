# ARCHITECTURE.md - Bus Charging Scheduler

## Design & Architecture Reference (v2)

---

## 1. Framework Choice: Discrete-Event Simulation with a Pluggable Rule Engine

### What the scheduler is

The scheduler is a **discrete-event simulation (DES)** driven by a **pluggable weighted scoring engine**,
operating over a **pluggable route topology layer**.

Think of it like an airport control tower. Chargers are runways. Buses are planes queued for landing.
When a runway clears, the tower scores every waiting plane on a set of priority rules (fuel level,
airline SLA, total airspace congestion) and picks the best one. The weights on those rules are dials
the operations team can turn. The tower's logic doesn't change if a new runway opens, a new airline
joins, or the airport moves — because the tower only talks to abstractions.

### Why Discrete-Event Simulation?

Three approaches were considered:

| Approach | Pros | Cons |
|---|---|---|
| Constraint Satisfaction (CP/SAT) | Globally optimal | Black box; can't defend individual decisions; hard to add soft rules |
| Static optimisation (LP/ILP) | Mathematically rigorous | Assumes complete upfront information; brittle to dynamic events |
| **Discrete-Event Simulation** | **Transparent; every decision traceable; natural for time-based problems; trivially extensible** | **Not globally optimal (greedy per event)** |

DES was chosen because:

1. **The problem is sequential in time.** Buses arrive, wait, charge, depart. Not a matrix — a timeline.
2. **Transparency.** During review, any scheduling decision can be walked through step by step: which bus
   was waiting, what each rule scored, which won.
3. **Extensibility.** A new event type (`CHARGER_FAILED`, `BUS_BOARDING_STOP`) is a new enum value
   and one handler function. The rest of the engine is untouched.
4. **"Adding a rule must not require rewriting the engine."** In DES, rules are scoring functions
   *called by* the engine. They don't live inside it. Adding a rule = one new class, one registration line.

---

## 2. The Three-Layer Architecture

The scheduler cleanly separates three independent concerns.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1: World Configuration (YAML)                                │
│  Route topology · Station hardware · Physics constants              │
│  Changes: rarely (new charger, road change)                         │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2: Scenario Configuration (YAML)                             │
│  Bus schedules · Operator weights · Rule weights                    │
│  Changes: every run                                                 │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3: Simulation Runtime (Python)                               │
│  Event loop · RouteProvider · WeightedScorer · Rule classes        │
│  Changes: never (only by adding new classes at the edge)            │
└─────────────────────────────────────────────────────────────────────┘
```

**Layer 1 (World):** Everything that defines the *physical infrastructure* — route topology, station
charger hardware, battery physics. Lives in `world/<id>.yaml`. An infra team owns these files. A
change here typically represents a physical installation or road change.

**Layer 2 (Scenario):** Everything that defines *this particular run* — which buses depart when,
operator fairness weights, rule tuning. Lives in `scenarios/scenario_N.yaml`. An ops team owns these.
Changing weights, adding buses, or trying a different priority strategy is a YAML edit, zero Python.

**Layer 3 (Runtime):** The simulation itself. The engine processes events against the state that
Layers 1+2 define. Adding a new soft rule, hard rule, or event type touches only the layer's *edge*
(a new class + one registration line). The engine loop, event dispatch, and scoring formula are stable.

For live local testing without Streamlit, `scripts/run_scenario.py` uses the same loader and engine
path in memory:

```bash
uv run python scripts/run_scenario.py scenarios/scenario_1.yaml --world-dir world
```

---

## 3. The RouteProvider Pattern (Strategy Pattern for Topology)

### The Problem with a Flat Segment List

The naive implementation stores `route.segments: List[RouteSegment]` directly in `Scenario`.
Every component that needs node positions, station order, or reachability must iterate those segments.

This causes three problems:
- **Coupling:** `planner.py`, `engine.py`, and `ScheduleContext` all know about raw segments.
- **No graph support:** Adding multiple valid paths between two stations requires a rewrite.
- **Dynamic re-routing impossible:** For charger failures (FC-25), the engine needs to ask
  "what can I reach from B with 120km range *right now*?" — a flat list cannot answer that efficiently.

### The RouteProvider Interface

```
RouteProvider (ABC)                         ← scheduler/models.py
  ├── origin                  (property)
  ├── destination             (property)
  ├── get_node_positions(dir) → Dict[str, float]
  ├── get_station_ids()       → List[str]
  ├── get_total_distance()    → float
  └── get_next_reachable_stations(from, dir, range_km) → List[str]

LinearRouteProvider(RouteProvider)          ← scheduler/routes/linear.py
  Implements the above for a single ordered segment list.
  All positions are pre-computed at construction — O(1) lookups.

GraphRouteProvider(RouteProvider)           ← scheduler/routes/graph.py  (V2)
  NetworkX-backed multi-path implementation.
  Supports alternate paths, shared stations across routes.
  The engine never changes — it calls the same interface.
```

**The engine, planner, and rules only interact with `RouteProvider` methods.**
Swapping `LinearRouteProvider` for `GraphRouteProvider` requires one change in the loader:
```python
# loader._parse_world():
if data["route"]["type"] == "graph":
    route = GraphRouteProvider(...)
else:
    route = LinearRouteProvider(...)
```
Zero changes anywhere else.

### How RouteProvider Enables Multi-Route Scheduling

With `RouteProvider` as an interface, `Scenario` can hold multiple routes:
```python
@dataclass
class Scenario:
    routes: Dict[str, RouteProvider]  # route_id → provider
    ...
```

A bus references its route:
```yaml
buses:
  - id: "bus-MK-01"
    route_id: "mysuru_kochi"
```

The engine dispatches:
```python
route = scenario.routes[bs.bus.route_id]
positions = route.get_node_positions(bs.bus.direction)
```

Buses on different routes share charging stations — `StationState` is route-agnostic by design.
The scoring rules see each bus's remaining distance on its own route, which is correct.

---

## 4. World vs Scenario Data Separation

### The Problem with One File

Mixing infrastructure data and run-specific data in `scenario_1.yaml` forces:
- Duplication of 80% of the file when only weights change.
- The infra team and ops team editing the same file — merge conflicts.
- Bulk "what-if" weight sweeps requiring 50 near-identical files.

### The Split

```
world/
└── bengaluru_kochi.yaml    # route topology, station hardware, physics
scenarios/
└── scenario_1.yaml         # bus schedules, weights, operators + "world_id: bengaluru_kochi"
```

**World file contains:**
- Route segments (topology)
- Station IDs and charger hardware (count, operational flags, availability windows)
- Physics constants (battery range, charge time, speed)

**Scenario file contains:**
- `world_id: "bengaluru_kochi"` (reference)
- `buses` block (who departs when)
- `operators` block (weights)
- `weights` block (rule tuning)

**Loader merges them:**
```python
def load_scenario(scenario_path: str, world_dir: str = "world") -> Scenario:
    config = _load_scenario_config(scenario_path)
    world  = _load_world(f"{world_dir}/{config['world_id']}.yaml")
    return _merge(config, world)
```

The engine receives a standard `Scenario` object — it never sees the split.

---

## 5. The Scoring System

### Formula

```
score(bus) = bus.weight × Σ_k( normalized_weight[k] × rule_k.score(bus, ctx) )
```

Configured weights are normalized by their registered total before rule scores are combined.

### The Soft Rules

| Rule | What it measures | Natural range | Use case |
|---|---|---|---|
| `IndividualWaitRule` | Minutes this bus has waited at this stop | [0, ~120] | Prevent starvation |
| `OperatorFairnessRule` | `op_weight × avg fleet delay` for this operator | [0, ~200] | SLA compliance |
| `OverallThroughputRule` | Remaining travel time to destination | [0, ~540] | Network efficiency |
| `DriverShiftProximityRule` | Urgency as shift-end approaches journey-end | [0, ~300] | Driver welfare |

Future rules such as `HeadwayRule` and `ElectricityCostRule` fit the same scoring interface but
are not implemented today.

**Scale warning:** `OverallThroughputRule` produces values 4–5× larger than `IndividualWaitRule`
at equal weights. Default equal weights are valid but biased toward throughput. For equal influence,
raise `individual` and `operator` relative to `overall`.

### Changing Weights and Adding Rules

The architecture keeps both operations at the edge of the system:

- Changing an existing rule weight is a scenario YAML edit. See the concrete examples in
  [OVERVIEW.md §5](OVERVIEW.md#5-code-reference-adding-a-soft-rule) and the scenario `weights`
  blocks.
- Adding a soft rule is one new `SoftRule` class, one scorer registration, and one optional
  `Weights` field. See [OVERVIEW.md §5](OVERVIEW.md#5-code-reference-adding-a-soft-rule).
- Adding a hard rule is a new `HardRule` implementation registered with the planner-side
  validation path. See [OVERVIEW.md §6](OVERVIEW.md#6-code-reference-adding-a-hard-rule).

The engine does not need rule-specific branches. It only calls the registered rule objects.

---

## 6. The Planner — Charging Plan Assignment

The planner enumerates all valid charging station sequences given the battery constraint.
"Valid" means no gap between consecutive checkpoints (origin → stations → destination) exceeds
`battery_range_km`.

**Valid 2-stop plans for BK (battery 240km, total 540km):**

| Plan | Gaps | Valid? |
|---|---|---|
| (A, C) | 100 · 220 · 220 | ✅ |
| (B, C) | 220 · 100 · 220 | ✅ |
| (B, D) | 220 · 220 · 100 | ✅ |
| (A, B) | 100 · 120 · 320 | ❌ last gap 320 > 240 |
| (A, D) | 100 · 340 · — | ❌ gap 340 > 240 |
| (C, D) | 320 · — · — | ❌ gap 320 > 240 |

`assign_charging_plans()` keeps minimum-stop candidates by default, then assigns buses with one
shared cross-direction charger pool. Buses are processed by predicted first-station arrival.
When all minimum-stop candidates exceed the world-configured extra-stop wait threshold, the planner
also considers one-stop-longer candidates. One-step lookahead breaks ties among equal-own-wait plans.

**V2 upgrade:** Use K-step lookahead, beam search, or ILP when deeper cascades matter.

---

## 7. Data Flow and Import Graph

```
models.py          ← defines all dataclasses, RouteProvider ABC
  ↑                   no scheduler imports
routes/linear.py   ← LinearRouteProvider; imports RouteSegment from models
routes/graph.py    ← GraphRouteProvider (V2); imports RouteProvider from models
rules/base.py      ← SoftRule, HardRule ABCs; TYPE_CHECKING imports from models
rules/soft_rules.py← imports SoftRule, BusState, ScheduleContext from models
rules/hard_rules.py← imports HardRule, RoutePositions from models
scoring.py         ← imports WeightedScorer; uses SoftRule, Weights, ScheduleContext
planner.py         ← imports Scenario, RouteProvider; no circular dependencies
engine.py          ← imports everything above; the top of the import hierarchy
loader.py          ← imports models, routes/; builds Scenario from YAML
app.py             ← imports engine, loader, ui components
```

**No circular imports. Every layer only imports from layers below it.**

---

## 8. Data Structure Design

The data model separates stable configuration, route topology, mutable simulation state, and
output records.

| Structure | Purpose | Why it is shaped this way |
|---|---|---|
| `Scenario` | Merged runtime input from world + scenario YAML | The engine gets one object and does not care which file a value came from. |
| `RouteProvider` | Abstract route topology API | Engine, planner, and rules ask for positions/distances/reachability without knowing whether the route is linear or graph-based. |
| `Bus` | Immutable trip request | Bus identity, operator, endpoints, departure, priority, and shift metadata stay stable during a run. |
| `Station` / `Charger` | Immutable physical hardware config | Hardware definition is separate from runtime queue/free-time state. |
| `BusState` | Mutable per-bus runtime state | Tracks current position, range, planned stop index, waits, and completed charge events. |
| `StationState` / `ChargerState` | Mutable station runtime state | Maintains charger availability, waiting queue, and station charge log independently of route direction. |
| `RoutePositions` | Planner validation input | Gives hard rules a compact distance view without coupling them to the full scenario. |
| `SimulationResult` | Stable output wrapper | Allows adding aggregate metrics without changing call sites that consume simulation output. |

This split is what keeps common changes local: YAML changes modify configuration objects,
planner changes modify plan assignment, and rule changes modify queue priority without rewriting
the event loop.

---

## 9. Directory Structure

```
bus_scheduler/
├── app.py
├── pyproject.toml
├── uv.lock
├── README.md
├── ARCHITECTURE.md         ← this file
├── FUTURE_CHANGES.md
├── OVERVIEW.md
├── PLAN.md
├── REFUTE.md
│
├── world/
│   └── bengaluru_kochi.yaml        # physical infrastructure (rarely changes)
│
├── scenarios/
│   ├── scenario_1.yaml             # Even Spacing (references bengaluru_kochi)
│   ├── scenario_2.yaml
│   ├── scenario_3.yaml
│   ├── scenario_4.yaml
│   ├── scenario_5.yaml
│   ├── scenario_6_intermediate_ab_ba.yaml
│   └── scenario_7_mixed_full_and_intermediate.yaml
│
├── scheduler/
│   ├── __init__.py
│   ├── models.py                   # ALL dataclasses + RouteProvider ABC + SimulationResult
│   ├── loader.py                   # YAML → Scenario (handles world/scenario split)
│   ├── planner.py                  # Valid plan generator (uses RouteProvider)
│   ├── engine.py                   # Discrete-event simulation
│   ├── scoring.py                  # WeightedScorer
│   │
│   ├── routes/
│   │   ├── __init__.py
│   │   └── linear.py               # LinearRouteProvider (V1 - current)
│   │
│   └── rules/
│       ├── __init__.py
│       ├── base.py                 # SoftRule, HardRule ABCs
│       ├── hard_rules.py           # RangeConstraint, StationOrderConstraint
│       └── soft_rules.py           # IndividualWait, OperatorFairness, OverallThroughput,
│                                   # DriverShiftProximity
│
└── ui/
    ├── __init__.py
    ├── scenario_view.py
    ├── bus_timetable.py
    └── station_view.py
```

---

## 10. Anticipated No-Code Changes

The current data structures were chosen so the following expected changes are data-only edits.
For the full roadmap, including changes that intentionally require new code, see
[FUTURE_CHANGES.md](FUTURE_CHANGES.md).

| Future change | Data edit | Why no code changes are needed |
|---|---|---|
| Add more buses to a run | Add entries under `scenarios/*.yaml` `buses` | The loader builds a `Bus` per row; the engine schedules all buses in `scenario.buses`. |
| Change departure times or endpoints | Edit `departure`, `origin_node`, `destination_node` | Direction and distances are derived through `RouteProvider`; no hardcoded bus list exists. |
| Change existing rule weights | Edit the scenario `weights` block | `WeightedScorer` reads weights dynamically by key from `Scenario.weights`. |
| Change operator priority | Edit `operators[].weight` | `OperatorFairnessRule` reads operator config at scoring time. |
| Change per-bus priority | Edit `buses[].weight` | `WeightedScorer` multiplies by `bus.weight`. |
| Add a charger to an existing station | Add another `stations[].chargers[]` row in the world file | `Station.chargers` is a tuple/list of hardware; `StationState` creates one runtime state per operational charger. |
| Change charger availability windows | Edit `available_from` / `available_until` | Loader converts windows to `ChargerState`; runtime charger checks use `can_charge_at()`. |
| Change battery range, charge time, or speed globally | Edit `world/*.yaml` `physics` | Planner and engine read `Scenario.physics`, not constants. |
| Add an intermediate station on the same linear route | Add route segments and a station row in the world file | `LinearRouteProvider` recomputes positions and the planner can enumerate the station. The planner keeps minimum-stop plans by default, but may use one extra stop when every minimum-stop candidate exceeds the configured wait threshold. |
| Add a new scenario over the same world | Add a new `scenarios/scenario_*.yaml` referencing `world_id` | Loader merges any scenario with the referenced world into the same `Scenario` shape. |

Changes such as graph routing, dynamic charger failure recovery, partial charging duration, or
new rule classes are supported by the architecture but still require code additions. Those are
documented in [FUTURE_CHANGES.md](FUTURE_CHANGES.md).

---

## 11. Project Assumptions

These separate problem givens from modeling choices and known technical limits.

### Given

Facts provided by the current problem setup.

| Given | Meaning |
|---|---|
| One Bengaluru-Kochi corridor | The current world models one ordered corridor from Bengaluru to Kochi. |
| Fixed charging stations on that corridor | Stations A-D are the charging points available to buses in the baseline world. |
| Shared physical stations across directions | BK and KB buses contend for the same station chargers; this is handled by one `StationState` per station ID. |
| Battery range and charge time are world inputs | Range, charge duration, and travel speed come from `world/*.yaml`, not code constants. |
| Scenario files define operations | Buses, departures, operators, and rule weights are run-specific scenario data. |
| Constant travel speed | Travel time is distance / `physics.travel_speed_kmh`; no traffic or segment speed variation is modeled. |

### Assumptions

Operational assumptions intentionally made for the current simulator.

| Assumption | Current meaning |
|---|---|
| Static route topology during a run | Roads and station order do not change mid-simulation. |
| Chargers do not fail mid-run | `operational` is loaded at startup; no runtime failure event exists. |
| Unlimited station waiting area | A station queue can grow without capacity rejection or diversion. |
| Origin charging is explicit | A bus only charges before departure when `requires_origin_charge: true`. |
| Soft rules arbitrate queues only | Hard feasibility is handled by planner rules; runtime queue priority is weighted soft scoring. |
| Time is deterministic minutes from midnight | Simulations are deterministic and can represent next-day arrivals, but no calendar/service-day logic exists. |

### Current Technical Assumptions / Future Enhancements

Technical limits that are acceptable for V1 and already have a path forward.

| Current technical assumption | Future enhancement |
|---|---|
| V1 uses `LinearRouteProvider`, so every bus travels forward along one active path. | `GraphRouteProvider` for multi-path routing. |
| En-route charging plans are assigned before simulation and not revised. | JIT station selection after each stop. |
| Planner allows at most one extra stop for congestion relief. | K-step, beam-search, or ILP planning over larger candidate sets. |
| Every completed charge resets range to full after one global `charge_time_minutes`. | Per-bus charge strategy and per-charger charge times. |
| Planner lookahead is intentionally limited to tie-breaking equal-own-wait plans for the next bus in predicted-arrival order. | K-step lookahead, beam search, or ILP. |
| Charger state changes only through queue/charge completion during a run. | FC-25 dynamic charger failure + JIT re-routing. |
| No station waiting capacity is enforced. | Station queue capacity + diversion logic. |
| No route service calendar exists. | Route service windows and day calendars. |

---

## 12. What Is Not Implemented in V1 (Own These Upfront)

| Feature | Schema ready? | Code ready? | Path to V2 |
|---|---|---|---|
| Driver shift enforcement | Field present | Soft scoring only | `DriverShiftHardRule` class, ~15 lines |
| Partial charging | Not present | Fixed-duration full charge only | Add strategy/capacity schema and update `_start_charging()` |
| Charger availability windows | Fields present | Enforced | Add maintenance/failure events if availability changes mid-run |
| Congestion-aware extra-stop selection | Threshold present in world YAML | One extra stop allowed when minimum-stop wait exceeds configured threshold | Ratio-based or experimentally tuned trigger |
| Score normalisation | Config weights present | Configured weights normalized by total | Rule-output normalization if needed |
| Graph-based routes | — | Not present | `GraphRouteProvider(RouteProvider)` |
| World/scenario YAML split | `world_id` reference | Implemented | Add more worlds/scenarios through YAML |
| Dynamic charger failure | — | Not present | Requires R-16 moderate fix (~50 lines) |
| Headway management | — | Not present | `HeadwayRule(SoftRule)`, ~20 lines |

---

## 13. Key Design Defences

**"Why not CP-SAT?"**
Solver is a black box. I can't walk through why bus-BK-03 got priority over bus-KB-07 at 21:15
if a solver chose it. DES makes every decision traceable to a specific event, score, and weight.

**"What if you need global optimality?"**
DES is greedy — it optimises per event, not globally. For a production system with SLAs, you'd
add a look-ahead scorer that simulates one time step ahead before committing. The scoring interface
is already pluggable enough to accommodate this.

**"Can you add a rule right now?"**
Yes. Write a class extending `SoftRule`, add one line to the scorer registration list in
`scheduler.engine.run_simulation()`, optionally add a defaulted field to `Weights`, and set that
weight in scenario YAML. A commented insertion template lives in `scheduler/rules/soft_rules.py`.
Hard plan constraints follow the same edge pattern: implement `HardRule` and register it in
`scheduler.planner.get_valid_charging_plans()`, with a commented template in
`scheduler/rules/hard_rules.py`. The event loop itself does not need rule-specific branches.

**"What breaks at 500 buses?"**
`ScheduleContext.get_operator_delays()` is O(B) per queue evaluation — becomes O(B²) at scale.
Fix: cache operator delay sums in `ScheduleContext`, update incrementally. ~15 lines. Already
documented as FC-18.

**"What if a charger breaks mid-run?"**
V1: known limitation — static plans don't recover. V2: JIT routing via
`route.get_next_reachable_stations()`. The `RouteProvider` interface already has this method.
The engine change is ~50 lines. See R-16 and FC-25.
