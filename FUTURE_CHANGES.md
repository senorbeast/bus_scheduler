# FUTURE_CHANGES.md - Roadmap

This file lists realistic changes from the current implementation state. The code today supports
one linear route, static en-route plans, fixed full-charge duration, charger availability windows,
origin charging, and weighted queue arbitration.

## Data-Only Changes Supported Now

### Add More Buses

Add rows under `scenarios/*.yaml` `buses`. The loader creates a `Bus` for every row and the
engine schedules all buses in the scenario.

### Add A New Scenario

Add `scenarios/scenario_<number>_<description>.yaml` with `meta.world_id: "bengaluru_kochi"`.
The original scenarios should remain unchanged unless explicitly requested.

### Change Rule Weights

Edit the scenario `weights` block:

```yaml
weights:
  individual: 2.0
  operator: 1.0
  overall: 0.5
  shift: 0.0
```

### Change Operator Fairness Weight

Edit `operators[].weight`. `OperatorFairnessRule` reads this at scoring time.

### Add A Charger To An Existing Station

Add another charger entry under `world/bengaluru_kochi.yaml` `stations[].chargers`. Runtime
`StationState` creates one `ChargerState` per operational charger.

### Change Charger Availability Windows

Edit `available_from` and `available_until`. The engine enforces these through
`ChargerState.can_charge_at()` and schedules `QUEUE_RECHECK` when needed.

### Add A Same-Line Intermediate Station

Split route segments in the world file and add a station entry. `LinearRouteProvider` recomputes
positions. The planner can enumerate the new station and may use it when range feasibility or the
one-extra-stop congestion threshold makes it useful.

## Near-Term Code Changes

### FC-01 - Per-Bus Battery Capacity

Current code has `initial_range_km`, which models starting charge, not maximum battery capacity.

Data change:

```yaml
buses:
  - id: "bus-BK-01"
    battery_range_km: 300
```

Code touch: planner range validation and engine full-charge reset should use the per-bus capacity
when present.

### FC-02 - Per-Charger Charge Duration

Data change:

```yaml
chargers:
  - id: "A-1"
    charge_time_minutes: 20
```

Code touch: add the field to `Charger`/`ChargerState`, then use it in `_start_charging()`.

### FC-03 - Partial Charging

The current engine uses fixed full-charge duration and every completed charge restores full range.
The earlier `Physics.charge_to_full` and `Bus.charge_strategy` placeholders were removed until
partial charging is implemented.

Code touch: compute required energy from the next target distance plus reserve, set a shorter
charge duration, and increase `current_range_km` by the charged amount.

### FC-04 - Driver Shift Hard Constraint

`DriverShiftProximityRule` is implemented as a soft queue rule. A hard shift rule would reject or
flag plans that cannot finish before shift end.

Code touch: add `DriverShiftHardRule` or a post-simulation validation pass.

### FC-05 - Adaptive Extra-Stop Trigger

The planner can use one extra charging stop when every minimum-stop option exceeds the
world-configured `planner.extra_stop_wait_threshold_minutes` value. That threshold is still a fixed
policy input.

Future alternatives:

- Ratio-based trigger, such as extra stops when predicted wait exceeds a multiple of charge time.
- Experimentally tuned threshold from historical scenarios or simulation sweeps.
- Per-station or per-time-window thresholds for known bottlenecks.

### FC-06 - Planner Awareness Of Charger Windows

The engine enforces charger windows exactly, but planner scoring primarily models charger free
times. This can make a range-valid plan look better than it will be at runtime.

Code touch: include `available_from`/`available_until` in `_score_plan()` and `_book_plan()`.

### FC-07 - Scorer-Side Hard Gates

The scorer currently supports weighted soft queue arbitration, not true must-pass queue gates. A
temporary workaround is to add a `SoftRule` and give it a very high scenario weight, but that still
participates in numeric tradeoffs and is not equivalent to a hard constraint.

Code touch: add an explicit queue-gating interface or pre-score filter for charger arbitration, then
decide whether rejected buses wait, are deferred until a condition changes, or are surfaced as
infeasible.

## Larger Architecture Changes

Soft-rule implementation convention: every real/user-facing `SoftRule` should have a matching
scenario-tunable weight. Add a field to `Weights`, parse `weights.<rule_key>` in the loader, include
the key in scenario YAML, and register the rule with the same key in `WeightedScorer`. The scorer has
a defensive fallback for missing keys, but new soft rules should not rely on it. Tests should cover
both nonzero weight behavior and `0.0` disabling the rule's influence.

### FC-08 - Dynamic Charger Failure And JIT Rerouting

Current static plans do not recover from runtime charger failures.

Data change:

```yaml
events:
  - type: "charger_failed"
    station_id: "C"
    at_time: "21:30"
```

Code touch: add a `CHARGER_FAILED` event, update charger state, and choose a new reachable live
station via `RouteProvider.get_next_reachable_stations()`.

### FC-09 - Multiple Routes Sharing Stations

Current `Scenario` holds one `RouteProvider`. Multi-route support needs buses to reference a route
and the scenario to hold multiple providers.

Data change:

```yaml
buses:
  - id: "bus-MK-01"
    route_id: "mysuru_kochi"
```

Code touch: add `Bus.route_id`, `Scenario.routes`, route lookup helpers, and planner/engine dispatch
through the bus route.

### FC-10 - Graph-Based Routing

Implement `GraphRouteProvider(RouteProvider)` for alternate paths and shared network hubs. Engine
and rule code should continue to call the same route interface.

### FC-11 - Station Queue Capacity And Diversion

Data change:

```yaml
stations:
  - id: "B"
    max_waiting: 3
```

Code touch: reject or divert arrivals when the queue is full, then select an alternate reachable
station when possible.

### FC-12 - Headway Management

Add `HeadwayRule(SoftRule)` to penalize buses that are too close behind another same-direction bus.
This needs route or scenario headway config and enough history in station logs to evaluate spacing.

### FC-13 - Electricity Cost Rule

Add tariff data to the world or scenario and implement `ElectricityCostRule(SoftRule)` using
`ScheduleContext.time_of_day`.

### FC-14 - Named Priority Classes

`Bus.priority_class` was removed because queue priority is currently controlled by `Bus.weight`.
Reintroduce a named priority field only if the loader maps classes to explicit score multipliers
or rule behavior.

### FC-15 - Operator Display Names

`OperatorConfig.display_name` was removed from runtime models because the UI currently displays
operator IDs. Reintroduce display names when the UI needs a human-friendly label separate from the
stable operator ID.

### FC-16 - Real-Time Priority Overrides

`ScheduleContext.priority_overrides` was removed because no UI/API set it. Reintroduce an override
map when live dispatch controls need to temporarily boost or suppress individual buses.

### FC-17 - Global Or Deeper Planner Optimization

The current planner is greedy with 1-step lookahead. Larger or tighter fleets may need:

- K-step lookahead.
- Beam search.
- Simulation feedback loops.
- ILP-based global plan assignment.

These can replace `assign_charging_plans()` while preserving the engine contract of
`dict[bus_id, list[station_id]]`.

## Implemented Roadmap Items

- World/scenario YAML split.
- RouteProvider abstraction for the current linear route.
- SimulationResult output wrapper with aggregate wait metrics.
- Charger availability-window enforcement.
- Origin charging for short station-origin trips.
- Predicted-arrival planner ordering.
- Shared cross-direction charger pool in planner predictions.
- One-step planner lookahead.
- One-extra-stop congestion relief threshold.
- Configurable one-extra-stop wait threshold in world YAML.
- Configured scorer weight normalization.
