# UPDATE-2-1.md - Planner Overhaul Status

This note records the planner behavior that is implemented now. It supersedes the old
`select_charging_plan()` and round-robin design.

## What Changed

The active planner entry point is:

```python
assign_charging_plans(scenario) -> dict[str, list[str]]
```

It replaced the earlier per-bus round-robin plan selector.

## Implemented Behavior

`assign_charging_plans()` now:

- Enumerates valid en-route charging plans per bus, including intermediate endpoint trips.
- Uses `StationOrderConstraint` and `RangeConstraint` for feasibility.
- Maintains one shared charger pool across both directions.
- Processes buses by predicted first en-route station arrival, not scenario order.
- Uses 1-step lookahead as a tiebreaker.
- Allows one extra stop when all minimum-stop candidates exceed the world-configured wait threshold.

The lookahead is deliberately conservative. A bus may choose among equal-own-wait plans based on
the next bus's projected cost, but it will not accept avoidable personal wait solely to improve
the next bus.

## What Remains Open

- Lookahead depth is one bus.
- Assignments are not revisited after later buses are planned.
- Charger availability windows are enforced by the engine but only approximated by the planner.
- Dynamic charger failures still require JIT rerouting in the engine.
- Full global optimality would require ILP, beam search, or another backtracking planner.

## Files Involved

- `scheduler/planner.py`
- `scheduler/rules/hard_rules.py`
- `scheduler/engine.py`
- `tests/test_scheduler.py`

## Related Docs

- `PLAN.md` describes the current planner in the full implementation context.
- `REFUTE.md` lists the remaining planning risks.
- `FUTURE_CHANGES.md` keeps the roadmap items for deeper lookahead, ILP, and simulation feedback.
