---
name: add-planner-hard-rule
description: Add a new hard charging-plan constraint to the bus scheduler planner. Use when Codex is asked to create, modify, or test a planner-side HardRule that rejects invalid candidate charging plans before simulation, such as max stops, required/forbidden station sequences, shift feasibility, or route feasibility constraints.
---

# Add Planner Hard Rule

Use this skill to add a planner-side `HardRule`. Hard rules are binary filters over candidate
charging station plans before simulation starts.

## Workflow

1. Inspect current planner and rule code:
   - `scheduler/rules/base.py`
   - `scheduler/rules/hard_rules.py`
   - `scheduler/planner.py`
   - `tests/test_scheduler.py`
2. If any rule-design data is missing, ask the user before editing:
   - Constraint intent in one sentence.
   - Concrete pass and fail examples.
   - Whether existing committed scenarios must remain valid.
   - Whether the rule applies globally or only after adding new scenario/world config.
3. Implement a `HardRule` subclass in `scheduler/rules/hard_rules.py`.
   - Use `is_satisfied(stations: list[str], rp: RoutePositions, battery_range: float) -> bool`.
   - Return `True` when the candidate plan is allowed.
   - Keep the rule deterministic and independent of live simulation queue state.
4. Register the rule in `scheduler.planner.get_valid_charging_plans()` in the local `rules` list.
5. Add focused tests in `tests/test_scheduler.py`.
   - Include at least one accepted and one rejected candidate.
   - If existing scenarios should remain valid, run all scenario completion tests.
6. Run:

```bash
uv run python -m unittest discover -s tests
uv run python -m compileall app.py scheduler ui tests scripts
```

## Template

Use `assets/hard_rule_template.py.txt` as a starter when useful. Do not copy it blindly; adapt the
class name, `name`, and rule logic to the requested constraint.
