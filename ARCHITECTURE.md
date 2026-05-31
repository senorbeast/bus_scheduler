# ARCHITECTURE.md — Bus Charging Scheduler

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
RouteProvider (ABC)                         ← scheduler/routes/base.py
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
score(bus) = priority_override(bus) × bus.weight × Σ_k( weights[k] × rule_k.score(bus, ctx) )
```

`priority_override` defaults to 1.0; set to >1.0 for real-time escalation (FC-27).

### The Soft Rules

| Rule | What it measures | Natural range | Use case |
|---|---|---|---|
| `IndividualWaitRule` | Minutes this bus has waited at this stop | [0, ~120] | Prevent starvation |
| `OperatorFairnessRule` | `op_weight × avg fleet delay` for this operator | [0, ~200] | SLA compliance |
| `OverallThroughputRule` | Remaining travel time to destination | [100, 540] | Network efficiency |
| `DriverShiftProximityRule` | Urgency as shift-end approaches journey-end | [0, ~300] | Driver welfare |
| `HeadwayRule` | Penalises buses bunching too close behind the one ahead | [-100, 0] | Passenger spacing |
| `ElectricityCostRule` | Penalises peak-hour charging | [-50, +10] | Cost optimisation |

**Scale warning:** `OverallThroughputRule` produces values 4–5× larger than `IndividualWaitRule`
at equal weights. Default `1.0/1.0/1.0` is correct per spec but biased toward throughput.
For equal influence: `individual: 4.0, operator: 2.0, overall: 1.0`.

### Adding a New Soft Rule in Under 2 Minutes

```python
# 1. Write the class — scheduler/rules/soft_rules.py
class ElectricityCostRule(SoftRule):
    name = "electricity_cost"
    def score(self, bus_state, context) -> float:
        peak_start, peak_end = 18 * 60, 22 * 60
        if peak_start <= context.time_of_day <= peak_end:
            return -50.0
        return 10.0

# 2. Register — app.py (one line)
(ElectricityCostRule(), "electricity"),

# 3. Add weight with default — models.py (one line)
electricity: float = 1.0

# 4. Tune — scenario YAML (one line)
weights:
  electricity: 1.5
```

**Total: 1 class, 1 import, 1 list entry, 1 dataclass field, 1 YAML line.**

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

Round-robin distribution: bus-BK-01 → (A,C), bus-BK-02 → (B,C), bus-BK-03 → (B,D),
bus-BK-04 → (A,C), … This pre-distributes traffic across all valid plans.

**V2 upgrade:** Query live `StationState` queue depths and prefer plans routing toward less-loaded stations.

---

## 7. Data Flow and Import Graph

```
models.py          ← defines all dataclasses, RouteProvider ABC
  ↑                   no scheduler imports
routes/base.py     ← RouteProvider ABC (redundant with models if kept there)
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

## 8. Directory Structure

```
bus_scheduler/
├── app.py
├── requirements.txt
├── README.md
├── ARCHITECTURE.md         ← this file
├── ASSUMPTIONS.md
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
│   └── scenario_5.yaml
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
│   │   ├── linear.py               # LinearRouteProvider (V1 — current)
│   │   └── graph.py                # GraphRouteProvider  (V2 — future)
│   │
│   └── rules/
│       ├── __init__.py
│       ├── base.py                 # SoftRule, HardRule ABCs
│       ├── hard_rules.py           # RangeConstraint, StationOrderConstraint
│       └── soft_rules.py           # IndividualWait, OperatorFairness, OverallThroughput,
│                                   # DriverShiftProximity, HeadwayRule (when enabled)
│
└── ui/
    ├── __init__.py
    ├── scenario_view.py
    ├── bus_timetable.py
    └── station_view.py
```

---

## 9. What Is Not Implemented in V1 (Own These Upfront)

| Feature | Schema ready? | Code ready? | Path to V2 |
|---|---|---|---|
| Driver shift enforcement | ✅ field present | Schema only | `DriverShiftHardRule` class, ~15 lines |
| Partial charging (`charge_to_full: false`) | ✅ flag present | Flag exists | ~15 lines in `_start_charging()` |
| Charger availability windows | ✅ fields present | Not enforced | `ChargerState.can_charge_at()` + 1 call site |
| Congestion-aware plan selection | — | Round-robin only | Pass `station_loads` to `select_charging_plan()` |
| Score normalisation | — | Documented | `NormalisedWeightedScorer` wrapper |
| Graph-based routes | — | Not present | `GraphRouteProvider(RouteProvider)` |
| World/scenario YAML split | — | Not present | Loader refactor (~30 lines) |
| Dynamic charger failure | — | Not present | Requires R-16 moderate fix (~50 lines) |
| Headway management | — | Not present | `HeadwayRule(SoftRule)`, ~20 lines |

---

## 10. Key Design Defences (Interview Prep)

**"Why not CP-SAT?"**
Solver is a black box. I can't walk through why bus-BK-03 got priority over bus-KB-07 at 21:15
if a solver chose it. DES makes every decision traceable to a specific event, score, and weight.

**"What if you need global optimality?"**
DES is greedy — it optimises per event, not globally. For a production system with SLAs, you'd
add a look-ahead scorer that simulates one time step ahead before committing. The scoring interface
is already pluggable enough to accommodate this.

**"Can you add a rule right now?"**
Yes. Write a class extending `SoftRule`, add one line to the rules list in `app.py`, optionally
add a field with default to `Weights`. The engine never changes.

**"What breaks at 500 buses?"**
`ScheduleContext.get_operator_delays()` is O(B) per queue evaluation — becomes O(B²) at scale.
Fix: cache operator delay sums in `ScheduleContext`, update incrementally. ~15 lines. Already
documented as FC-18.

**"What if a charger breaks mid-run?"**
V1: known limitation — static plans don't recover. V2: JIT routing via
`route.get_next_reachable_stations()`. The `RouteProvider` interface already has this method.
The engine change is ~50 lines. See R-16 and FC-25.