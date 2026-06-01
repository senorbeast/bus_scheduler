# OVERVIEW.md - Master Reference

---

## 1. The Problem in Three Sentences

Electric buses run on a 540km linear route (`Bengaluru -> A -> B -> C -> D -> Kochi`) with a
240km full-charge range. Full-corridor buses need multiple intermediate charging stops, while
short trips such as `A -> B` can still contend for origin chargers before departure. The scheduler
decides charger order with a deterministic discrete-event simulation and weighted soft rules.

---

## 2. Scoring Criteria — Priority Order

| Priority | Area | What They Test | Where Handled |
|---|---|---|---|
| **1** | **Approach** | Is DES the right fit? Can you defend over CP-SAT? | `ARCHITECTURE.md §1` |
| **2** | **Scalability** | Can you add a new rule live without engine changes? | `ARCHITECTURE.md §5` |
| **3** | **Weight tunability** | Is changing a weight one YAML value? | `scenarios/*.yaml` weights block |
| **4** | **Data modeling & foresight** | Did you anticipate future changes? | `ARCHITECTURE.md §8` + `ARCHITECTURE.md §10` + `FUTURE_CHANGES.md` |
| **5** | **Correctness** | Does every schedule obey the 240km rule? | Planner guarantees valid plans |
| **6** | **Code quality** | Readable, extensible, typed? | `PLAN.md` (typed dataclasses, RouteProvider) |
| **7** | **Docs** | Honest about done vs not-done? | `README.md`, `PLAN.md`, `REFUTE.md` |

---

## 3. High-Level Algorithm

> *"The scheduler runs as a discrete-event simulation, which is the natural fit for a problem
> that's fundamentally about things happening at points in time.*
>
> *At startup, each bus is assigned a static en-route charging plan for its own endpoints. The
> planner enumerates valid station sequences, keeps minimum-stop plans by default, and may allow
> one extra stop when predicted waits are severe. We then create departure or origin-charge events
> for each bus.*
>
> *Those events go into a priority queue sorted by time. We process them one by one. When a bus
> arrives at a station: if the charger is free, it starts charging; if not, it joins a waiting
> queue. When charging ends, the charger is freed — and if there's a queue, we score every waiting
> bus using a weighted sum of soft rules (individual wait time, operator fleet delay, remaining
> journey) and the highest scorer charges next.*
>
> *The route topology is behind a RouteProvider interface - the engine never sees raw segments.
> That's what lets us swap a linear route for a graph-based multi-route network without touching
> the engine. Rule weights are a single YAML block. Adding a new priority rule is a
> new Python class plus one registration line. The engine never changes."*

---

## 4. Architecture in Half a Page

**Pattern:** Discrete-Event Simulation + Pluggable Rule Engine + RouteProvider Strategy Pattern

**Three clean layers:**

**1. Configuration (YAML world + scenario files)**
World file: route topology, station hardware, physics. Scenario file: bus schedules, weights.
Adding a station, doubling chargers, or changing battery range is a YAML edit for data loading.
Planner policy is separate: the current planner prefers minimum-stop plans, but can use one
extra stop when all minimum-stop options exceed the world-configured wait threshold.

**2. Route Layer (scheduler/routes/)**
`RouteProvider` abstract interface. `LinearRouteProvider` for current single-path route.
`GraphRouteProvider` (V2) for multi-path topology. Engine, planner, and rules call only the
interface — they're unaware of the underlying implementation.

**3. Simulation (engine.py + rules/)**
Heap-based event queue processes `BUS_READY_TO_DEPART`, `BUS_ARRIVES_AT_STATION`,
`CHARGING_COMPLETE`, and `QUEUE_RECHECK` in time order. Contention
resolved by `WeightedScorer` which calls registered `SoftRule.score()` functions and combines as
`bus.weight × Σ(normalized_weight_k × rule_k.score())`. Rules are injected; the engine
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
# scheduler/engine.py - one new line in scorer construction
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
| `OverallThroughputRule` | Remaining travel time to destination | [0, ~540] | minutes |
| `DriverShiftProximityRule` | Urgency as remaining shift approaches journey time | [0, ~300] | urgency-units |

Future rules such as `HeadwayRule` and `ElectricityCostRule` fit the same interface but are not
implemented today.

**Scale warning:** configured weights are normalized by their sum, but the underlying rule scores
still have different natural ranges. `OverallThroughputRule` can produce larger raw values than
`IndividualWaitRule`, so equal normalized weights do not guarantee equal behavioral influence.

**Scores combine as:**
```
final_score = bus.weight × (
    norm_w_individual × S_individual
  + norm_w_operator   × S_operator
  + norm_w_overall    × S_overall
  + norm_w_shift      × S_shift
)
```

---

## 9. Time and Space Complexity

The v1 implementation supports one linear route with arbitrary bus endpoints on that route.
For a bus trip, let `S_trip` be the number of candidate charging stations strictly between
that bus's origin and destination.

**Planner time complexity:** `O(2^S_trip * S_trip)` per bus. The planner enumerates every
station subset and validates the distance gaps in that subset. This is acceptable for the
current four-station corridor. For large networks, replace subset enumeration with dynamic
programming or graph shortest-path search over reachable charging stops.

**Simulation time complexity:** approximately `O(E log E + Q * R + Q * B)` per run, where
`E` is the number of simulation events, `Q` is the number of queue arbitration operations,
`R` is the number of soft rules, and `B` is the number of buses. The `E log E` term comes
from heap event scheduling. The `Q * B` term is from the current operator fairness rule,
which scans bus history through `ScheduleContext.get_operator_delays()`.

**Space complexity:** `O(B + C + E + L)`, where `B` is bus state count, `C` is charger state
count, `E` is queued future events, and `L` is completed charging log entries.

**Scale note:** For 500+ buses, cache operator delay totals/counts incrementally in
`ScheduleContext` to reduce operator fairness scoring from `O(B)` to `O(1)` per score call.
