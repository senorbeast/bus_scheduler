# REFUTE.md - Current Gaps and Architecture Risks

This file records current limitations after comparing `scheduler/` and `scenarios/` against the
documentation. Items marked fixed are implemented in the code today.

## Fixed Since The Original Design

| Item | Current status |
|---|---|
| Inter-day time formatting | Fixed by `minutes_to_time_str()` with `+Nd` prefixes. |
| Mutable bus arrival/wait state | Fixed by `BusState.station_arrival_time`, `current_plan_index`, and `completed_events`. |
| Range decrement during travel | Fixed in `_schedule_next_travel()`. |
| Deterministic event tie ordering | Fixed by `Event.sequence`. |
| Single charging plan for all buses | Fixed by `assign_charging_plans()`. |
| Hard rules unused | Fixed. Planner calls `StationOrderConstraint` and `RangeConstraint`. |
| Midnight-crossing shifts | Fixed by `driver_shift_end_minutes()`. |
| World/scenario YAML conflation | Fixed. Scenarios reference `meta.world_id`; loader merges with `world/*.yaml`. |
| RouteProvider abstraction | Fixed. `RouteProvider` lives in `scheduler.models`; `LinearRouteProvider` lives in `scheduler/routes/linear.py`. |
| Planner layer boundary | Fixed. `ScheduleContext.get_remaining_distance()` uses `scenario.route`. |
| Bare tuple simulation result | Fixed. `run_simulation()` returns `SimulationResult`. |
| Charger availability helper | Fixed by `ChargerState.can_charge_at()` and queue rechecks. |
| Direction-only trips | Fixed. Buses may use explicit `origin_node` and `destination_node`. |
| Departure-order planner bias | Fixed. Planner orders by predicted first station arrival. |
| Minimum-stop-only planner | Partially fixed. One extra stop is allowed when minimum-stop predicted wait exceeds the world-configured threshold. |

## Open Risks

### R-01 - Static Plans Do Not Recover From Runtime Charger Failures

`assign_charging_plans()` produces an en-route stop list before simulation starts. If a charger
fails after planning, the engine has no failure event and no JIT rerouting step. This is acceptable
for current scenarios because charger operational status is static, but it is the main correctness
gap for live operations.

Fix path: add a `CHARGER_FAILED` event and choose the next reachable live station via
`RouteProvider.get_next_reachable_stations()` before each leg.

### R-02 - Partial Charging Is Not Implemented

The engine always uses `scenario.physics.charge_time_minutes`, and `_handle_charge_complete()`
always restores full range. Earlier placeholder fields for partial charging were removed from the
runtime model until the behavior is implemented.

Fix path: compute charge duration from range needed to reach the next target plus reserve, then
increase `current_range_km` by the charged amount instead of always setting full range.

### R-03 - Driver Shift Is Soft Only

`DriverShiftProximityRule` changes queue priority, but there is no hard rule preventing a plan or
schedule that exceeds the driver's shift.

Fix path: add a planner or post-simulation hard validation rule for shift feasibility.

### R-04 - Single Linear Route Only

The route is behind an interface, but only `LinearRouteProvider` is implemented and `Scenario`
contains one route. There is no `route_id` on `Bus` and no multi-route world schema.

Fix path: add `Scenario.routes`, `Bus.route_id`, and a graph or multi-route provider while keeping
station states keyed by shared station ID.

### R-05 - Planner Is Still Greedy

The planner has a shared pool and 1-step lookahead, but assignments are one-shot and are not
revisited. It can miss N -> N+2 or wider cascades.

Fix path: K-step lookahead, beam search, iterative simulation feedback, or ILP.

### R-06 - Planner Availability Model Is Simpler Than Engine Availability

The planner seeds station capacity from active charger count and predicts free times, but it does
not model daily charger windows as precisely as the engine. A plan can be feasible by range but
still queue until a charger's next available window.

Fix path: include charger `available_from`/`available_until` in planner scoring or keep this as
runtime-only delay behavior.

### R-07 - Rule Output Scales Are Not Normalized

Configured scorer weights are normalized by their total, but raw rule outputs still have different
natural scales. `OverallThroughputRule` can return larger values than `IndividualWaitRule` at equal
normalized weights.

Fix path: add rule-output normalization or calibrate default weights by scenario type.

### R-08 - Station Queues Are Unlimited

There is no max waiting capacity or diversion behavior. This is fine for the current simulator but
not for constrained depots or roadside charging sites.

Fix path: add station queue capacity and diversion/JIT station selection.

## Current Assumptions

- Route topology does not change during a run.
- Charger operational flags are loaded at startup; no runtime failure event exists.
- Travel speed is constant across the route.
- Charging duration is global and fixed.
- Origin charging happens only when `requires_origin_charge: true`.
- Scenario IDs and world IDs are trusted to be unique.
