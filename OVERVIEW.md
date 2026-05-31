# OVERVIEW.md — Master Reference (v2)

---

## 1. The Problem in Three Sentences

Electric buses run a 540km route (Bengaluru → A → B → C → D → Kochi) on a 240km battery, so every bus
**must charge at exactly two or more intermediate stations** to complete the trip. Stations A–D each have
one charger, so when multiple buses arrive at roughly the same time, the scheduler must decide who charges
first and who waits. Your job is to build the system that makes those decisions — minimising wait times
while keeping operator fleets and the overall network running smoothly — and to build it so that future
changes (more stations, more buses, new routes, new priority rules) are trivially cheap.

---

## 2. Scoring Criteria — Priority Order

| Priority | Area | What They Test | Where Handled |
|---|---|---|---|
| **1** | **Approach** | Is DES the right fit? Can you defend over CP-SAT? | `ARCHITECTURE.md §1` |
| **2** | **Scalability** | Can you add a new rule live without engine changes? | `ARCHITECTURE.md §5` |
| **3** | **Weight tunability** | Is changing a weight one YAML value? | `scenarios/*.yaml` weights block |
| **4** | **Data modeling & foresight** | Did you anticipate future changes? | `ARCHITECTURE.md §9` + `FUTURE_CHANGES.md` |
| **5** | **Correctness** | Does every schedule obey the 240km rule? | Planner guarantees valid plans |
| **6** | **Code quality** | Readable, extensible, typed? | `PLAN.md` (typed dataclasses, RouteProvider) |
| **7** | **Docs** | Honest about done vs not-done? | `README.md` Known Limitations |

---

## 3. High-Level Algorithm (Interview Opener — ~60 seconds)

> *"The scheduler runs as a discrete-event simulation, which is the natural fit for a problem
> that's fundamentally about things happening at points in time.*
>
> *At startup, each bus is assigned a minimum-stop charging plan — the fewest stations it must
> stop at to cover 540km on a 240km battery. For this route that's always two stops, and there
> are three valid 2-stop plans per direction. We then create one event per bus: 'this bus arrives
> at its first charging station at time T.'*
>
> *Those events go into a priority queue sorted by time. We process them one by one. When a bus
> arrives at a station: if the charger is free, it starts charging; if not, it joins a waiting
> queue. When charging ends, the charger is freed — and if there's a queue, we score every waiting
> bus using a weighted sum of soft rules (individual wait time, operator fleet delay, remaining
> journey) and the highest scorer charges next.*
>
> *The route topology is behind a RouteProvider interface — the engine never sees raw segments.
> That's what lets us swap a linear route for a graph-based multi-route network without touching
> the engine. The three rule weights are a single YAML block. Adding a new priority rule is a
> new Python class plus one registration line. The engine never changes."*

---

## 4. Architecture in Half a Page

**Pattern:** Discrete-Event Simulation + Pluggable Rule Engine + RouteProvider Strategy Pattern

**Three clean layers:**

**1. Configuration (YAML world + scenario files)**
World file: route topology, station hardware, physics. Scenario file: bus schedules, weights.
Adding a station, doubling chargers, or changing battery range is a YAML edit. Zero Python.

**2. Route Layer (scheduler/routes/)**
`RouteProvider` abstract interface. `LinearRouteProvider` for current single-path route.
`GraphRouteProvider` (V2) for multi-path topology. Engine, planner, and rules call only the
interface — they're unaware of the underlying implementation.

**3. Simulation (engine.py + rules/)**
Heap-based event queue processes `BUS_ARRIVES` and `CHARGING_COMPLETE` in time order. Contention
resolved by `WeightedScorer` which calls registered `SoftRule.score()` functions and combines as
`priority_override × bus.weight × Σ(weight_k × rule_k.score())`. Rules are injected; the engine
doesn't name them. Adding a rule = one class, one list entry. Hard rules validate plans before
simulation; soft rules govern queue priority during simulation.

---

## 5. Code Reference: Adding a Soft Rule

```python
# scheduler/rules/soft_rules.py — add this class
class ElectricityCostRule(SoftRule):
    """Off-peak charging gets priority. Plug into WeightedScorer with key 'electricity'."""
    name: str = "electricity_cost"

    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        peak_start: float = 18.0 * 60
        peak_end:   float = 22.0 * 60
        return -50.0 if peak_start <= context.time_of_day <= peak_end else 10.0
```

```python
# app.py — one new line in scorer construction
scorer = WeightedScorer(
    weights=scenario.weights,
    rules=[
        (IndividualWaitRule(),    "individual"),
        (OperatorFairnessRule(),  "operator"),
        (OverallThroughputRule(), "overall"),
        (ElectricityCostRule(),   "electricity"),   # ← add this
    ]
)
```

```python
# scheduler/models.py — one new field with default
@dataclass
class Weights:
    individual:  float = 1.0
    operator:    float = 1.0
    overall:     float = 1.0
    electricity: float = 1.0   # ← add this
```

```yaml
# scenario YAML — one new line
weights:
  electricity: 1.5
```

**Total changes: 1 class (15 lines), 1 import, 1 list entry, 1 dataclass field, 1 YAML line.**

---

## 6. Code Reference: Adding a Hard Rule

```python
# scheduler/rules/hard_rules.py
class DriverShiftHardRule(HardRule):
    """
    Plan-time: validates the bus can complete its journey before the driver's shift ends.
    Called by planner._is_valid_plan() for each candidate plan.
    """
    name: str = "driver_shift"

    def is_satisfied(
        self, stations: List[str], rp: RoutePositions, battery_range: float,
    ) -> bool:
        return True   # plan-time check; runtime check done separately in engine
```

Runtime enforcement in `engine.py`:
```python
def _validate_driver_shift(bus_state: BusState, estimated_arrival: float) -> bool:
    if bus_state.bus.driver_shift is None:
        return True
    return estimated_arrival <= driver_shift_end_minutes(bus_state.bus.driver_shift)
```

---

## 7. Code Reference: Swapping Route Topology

```python
# To upgrade from linear to graph — only change is in loader:
# loader._parse_world():
if data["route"]["type"] == "graph":
    route = GraphRouteProvider(edges=data["route"]["edges"], ...)
else:
    route = LinearRouteProvider(segments=segments, station_ids=station_ids)

# Engine, planner, and rules are unchanged.
# They call route.get_node_positions(), route.get_station_ids(), etc.
```

---

## 8. Soft Rule Score Reference

| Rule | What It Returns | Natural Range | Unit |
|---|---|---|---|
| `IndividualWaitRule` | Minutes this bus has waited at this station | [0, ~120] | minutes |
| `OperatorFairnessRule` | `op_weight × avg fleet delay` for this operator | [0, ~200] | weighted-minutes |
| `OverallThroughputRule` | Remaining travel time to destination | [100, 540] | minutes |
| `DriverShiftProximityRule` | Urgency as remaining shift approaches journey time | [0, ~300] | urgency-units |
| `HeadwayRule` | Penalty when bus is too close behind the preceding bus | [-100, 0] | penalty-units |
| `ElectricityCostRule` | Incentive to charge off-peak | [-50, +10] | cost-units |

**Scale warning:** `OverallThroughputRule` produces values 4–5× larger than `IndividualWaitRule` at
equal weights. Document this; recommend `individual: 4.0, overall: 1.0` for equal influence.

**Scores combine as:**
```
final_score = priority_override × bus.weight × (
    w_individual × S_individual
  + w_operator   × S_operator
  + w_overall    × S_overall
)
```

---

## 9. Interview Prep Checklist

### Rehearse verbally

- [ ] **60-second DES pitch** (§3 above)
- [ ] **Why not CP-SAT?** "Solver is a black box; DES makes every decision traceable to a specific score at a specific time."
- [ ] **RouteProvider pitch:** "The route is behind an interface. Swapping linear for graph-based routing is one line in the loader. The engine, rules, and planner never change."
- [ ] **Walk through valid plan math** live: A→C gaps 100/220/220, all ≤ 240 ✓
- [ ] **Add `ElectricityCostRule` in under 2 minutes** — practice cold
- [ ] **World vs scenario split:** "Infra team owns `world/bengaluru_kochi.yaml`. Ops team owns `scenario_1.yaml`. They never merge-conflict."

### Demonstrate in the app

- [ ] Scenario 1 → verify each bus charged exactly twice
- [ ] Scenario 4 vs 1 → show operator weight change affects KPN bus priority
- [ ] Scenario 5 → non-zero wait times at shared stations
- [ ] Live: add a new scenario (copy YAML, change departures + weights)

### Questions you'll be asked

- "Add a route between Mysuru and Kochi sharing Station B" → new world file + `route_id` on buses
- "What if KPN wants priority?" → `operators.kpn.weight: 2.0` in YAML
- "Add driver shift rule" → `DriverShiftProximityRule(SoftRule)` class + register
- "What if the B charger fails mid-run?" → V1 limitation (static plans). V2: `CHARGER_FAILED` event + JIT routing via `RouteProvider.get_next_reachable_stations()`.
- "What breaks at 500 buses?" → `get_operator_delays()` O(B). Fix: incremental cache in `ScheduleContext`. ~15 lines.
- "Support pick-up stops at Station A?" → New `stop_type: "boarding_and_charging"` field. New `BUS_BOARDING_STOP` event type. Engine adds fixed dwell logic.

### Known weaknesses to get ahead of

- "Why do buses follow a static plan?" → Own it. "V1 assigns plans at startup — correct for the baseline scenario. V2 uses JIT routing via `RouteProvider.get_next_reachable_stations()` for charger failure recovery. The interface is ready; the engine handler is the 50-line V2 item."
- "Score scales differ across rules" → Own it. "IndividualWait is 0–120, OverallThroughput is 100–540. A normaliser wrapper is the clean V2 fix. Documented in REFUTE.md R-07."
- "All buses use minimum-stop plans" → Own it. "Round-robin spreads buses across the 3 valid plans. Congestion-aware selection that reads live queue depths is the next upgrade — 10 lines in `select_charging_plan()`."