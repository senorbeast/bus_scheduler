# PLAN.md - Current Implementation Blueprint

This file describes the implementation that is present in the repository now. Historical
round-robin planner notes have been removed because the code now uses `assign_charging_plans()`.

## Project Structure

```text
bus_scheduler/
├── app.py
├── pyproject.toml
├── uv.lock
├── README.md
├── ARCHITECTURE.md
├── FUTURE_CHANGES.md
├── OVERVIEW.md
├── PLAN.md
├── REFUTE.md
├── UPDATE-2-1.md
├── world/
│   └── bengaluru_kochi.yaml
├── scenarios/
│   ├── scenario_1.yaml
│   ├── scenario_2.yaml
│   ├── scenario_3.yaml
│   ├── scenario_4.yaml
│   ├── scenario_5.yaml
│   ├── scenario_6_intermediate_ab_ba.yaml
│   └── scenario_7_mixed_full_and_intermediate.yaml
├── scheduler/
│   ├── models.py
│   ├── loader.py
│   ├── planner.py
│   ├── engine.py
│   ├── scoring.py
│   ├── routes/
│   │   └── linear.py
│   └── rules/
│       ├── base.py
│       ├── hard_rules.py
│       └── soft_rules.py
├── tests/
│   └── test_scheduler.py
└── ui/
    ├── scenario_view.py
    ├── bus_timetable.py
    ├── station_view.py
    └── formatting.py
```

## Data Model

The simulator uses a world/scenario split:

- `world/bengaluru_kochi.yaml` defines the physical corridor, station hardware, charger
  windows, and global physics.
- `scenarios/*.yaml` define run-specific metadata, operators, rule weights, and bus trips.
- `loader.load_scenario()` resolves `meta.world_id`, loads the matching world file, and
  returns one merged `Scenario`.

The active world is a single linear route:

```text
Bengaluru -> A -> B -> C -> D -> Kochi
100 km      120   100   120   100
```

Stations `A`, `B`, `C`, and `D` each have one charger. The route total is 540 km, battery
range is 240 km, charge time is 25 minutes, and speed is 60 km/h.

## Bus Trip Shape

Full-corridor buses can use the legacy direction-only form:

```yaml
- id: "bus-BK-01"
  operator: "kpn"
  direction: "BK"
  departure: "19:00"
```

The loader converts `BK` to `Bengaluru -> Kochi` and `KB` to `Kochi -> Bengaluru`.

Intermediate trips use explicit endpoints:

```yaml
- id: "bus-AB-01"
  operator: "kpn"
  origin_node: "A"
  destination_node: "B"
  departure: "19:00"
  requires_origin_charge: true
  initial_range_km: 40
```

When endpoints are provided, direction is derived from route position. `requires_origin_charge`
models a pre-service top-up at the origin station before the bus starts route travel.

## Planner

`scheduler.planner.assign_charging_plans()` is the current planner entry point.

For each bus it:

1. Enumerates all station subsets strictly between the bus endpoints.
2. Filters candidates through `StationOrderConstraint` and `RangeConstraint`.
3. Keeps minimum-stop candidates by default.
4. Adds one-stop-longer candidates when all minimum-stop options predict more than
   `scenario.planner.extra_stop_wait_threshold_minutes` of wait.
5. Assigns buses in predicted first-station-arrival order.
6. Scores candidates against one shared cross-direction charger pool.
7. Uses 1-step lookahead as a tiebreaker, without making a bus accept avoidable personal wait
   solely to help the next bus.

Plans are static once assigned. The engine does not reroute buses if a charger fails mid-run.

## Engine

`scheduler.engine.run_simulation()` runs a deterministic discrete-event simulation and returns
`SimulationResult`.

Current event types:

- `BUS_READY_TO_DEPART`
- `BUS_ARRIVES_AT_STATION`
- `CHARGING_COMPLETE`
- `QUEUE_RECHECK`

The engine maintains one `StationState` per physical station, so buses from both directions and
intermediate trips contend for the same charger. A charge event always restores range to the
global full battery range after the global charge duration.

Charger windows are enforced through `ChargerState.can_charge_at()` and
`ChargerState.next_available_time()`. If no charger is available because of a daily window, the
engine schedules `QUEUE_RECHECK` at the next available start time.

## Scoring

When a charger frees and multiple buses are queued, `WeightedScorer` computes:

```text
score = bus.weight * sum(normalized_weight[key] * rule.score(bus_state, context))
```

Registered soft rules:

- `IndividualWaitRule`
- `OperatorFairnessRule`
- `OverallThroughputRule`
- `DriverShiftProximityRule`

Scenario weights currently support:

- `individual`
- `operator`
- `overall`
- `shift`

Per-bus priority is controlled by `Bus.weight`. Removed future-facing placeholders such as named
priority classes and charge strategies are documented in `FUTURE_CHANGES.md`.

## Current Scenarios

- `scenario_1.yaml`: even full-corridor spacing.
- `scenario_2.yaml`: bunched full-corridor start.
- `scenario_3.yaml`: asymmetric full-corridor load.
- `scenario_4.yaml`: operator-heavy load with higher operator fairness weight.
- `scenario_5.yaml`: worst-case convergence.
- `scenario_6_intermediate_ab_ba.yaml`: concurrent A-B and B-A origin charging.
- `scenario_7_mixed_full_and_intermediate.yaml`: full-corridor buses mixed with A/B station-origin trips.

## Verification

Use the repository-standard commands:

```bash
uv run python -m unittest discover -s tests
uv run python -m compileall app.py scheduler ui tests
```

## Known V1 Boundaries

- One linear physical route is implemented. Graph and multi-route routing remain future work.
- En-route charging plans are static for the run.
- Dynamic charger failure events are not implemented.
- Station queue capacity is unlimited.
- Partial-charge duration is not implemented.
- Driver shifts affect queue scoring only; there is no hard shift feasibility rule.
- Planner lookahead depth is one bus and assignments are not revisited.
