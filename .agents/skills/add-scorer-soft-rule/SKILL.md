---
name: add-scorer-soft-rule
description: Add a new soft queue scoring rule to the bus scheduler scorer. Use when Codex is asked to create, modify, or test a SoftRule that affects charger queue priority, including adding its matching Weights field, loader parsing, scenario YAML weight key, scorer registration, and queue arbitration tests.
---

# Add Scorer Soft Rule

Use this skill to add a queue-priority `SoftRule`. In this project, every real/user-facing soft
rule must have a corresponding scenario-tunable weight.

## Required Clarifications

Before editing, ask the user if any of these are missing:

- Rule name and intent.
- What the rule measures from `BusState`, `ScheduleContext`, scenario data, or station state.
- Whether higher score means higher urgency.
- Expected score scale, especially relative to existing rules.
- YAML weight key to add under `weights`.
- Default value for the new `Weights` field.
- Which `scenarios/scenario_*.yaml` files should receive the new weight key.

Defaults if the user does not specify:

- Derive a snake_case weight key from the rule name.
- Use `Weights.<key>: float = 0.0` for opt-in rules.
- Use `1.0` only when the user explicitly wants existing scenarios affected by default.
- Add `weights.<key>` to all committed `scenarios/scenario_*.yaml` files unless the user limits scope.

## Workflow

1. Inspect current scorer and rule code:
   - `scheduler/rules/base.py`
   - `scheduler/rules/soft_rules.py`
   - `scheduler/scoring.py`
   - `scheduler/engine.py`
   - `scheduler/models.py`
   - `scheduler/loader.py`
   - `tests/test_scheduler.py`
2. Add a `SoftRule` subclass in `scheduler/rules/soft_rules.py`.
   - Use `score(self, bus_state: BusState, context: ScheduleContext) -> float`.
   - Return larger values for buses that should charge sooner.
   - Keep the score deterministic for the same context.
3. Add a matching field to `Weights` in `scheduler/models.py`.
4. Parse `weights.<key>` in `scheduler/loader.py`.
5. Register the rule in `scheduler.engine.run_simulation()` as `(NewRule(), "<key>")`.
6. Add the weight key to relevant scenario YAML files.
7. Add focused tests.
   - Prove a nonzero weight changes queue priority as intended.
   - Prove `0.0` removes the new rule's influence.
8. Run:

```bash
uv run python -m unittest discover -s tests
uv run python -m compileall app.py scheduler ui tests scripts
```

## Convention

`WeightedScorer` has a defensive `getattr(..., 1.0)` fallback for missing keys. Do not rely on it
for new project rules. User-facing soft rules should always be explicit in `Weights`, loader parsing,
scenario YAML, and tests.

## Template

Use `assets/soft_rule_template.py.txt` as a starter when useful. Adapt names, score scale, weight
key, tests, and scenario updates to the requested rule.
