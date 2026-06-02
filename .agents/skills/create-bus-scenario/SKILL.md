---
name: create-bus-scenario
description: Create a valid bus scheduler scenario YAML file. Use when Codex is asked to add a new scenarios/scenario_*.yaml file from user input, including metadata, world reference, operators, soft-rule weights, buses, origin charging, endpoints or directions, and validation with the local scenario runner.
---

# Create Bus Scenario

Use this skill to create a new `scenarios/scenario_*.yaml` file without changing original scenario
intent.

## Required Clarifications

Before writing YAML, ask the user if any required data is missing:

- Scenario ID, name, description, and `world_id`.
- Operators and operator weights.
- Rule weights under `weights`.
- Bus IDs, operators, departures, and either `direction` or explicit endpoints.
- Origin charging requirements and `initial_range_km` for station-origin trips.
- Driver shift windows, if shift urgency should matter.

Defaults if the user does not specify:

- Use the existing `bengaluru_kochi` world.
- Use operators from `scenarios/scenario_1.yaml`.
- Use existing default weights: `individual: 1.0`, `operator: 1.0`, `overall: 1.0`, `shift: 0.0`.
- Prefer explicit `origin_node` and `destination_node` when endpoints are provided.
- Use `direction: BK` or `direction: KB` only for full-corridor trips.

## Workflow

1. Inspect `world/<world_id>.yaml` and at least one similar scenario.
2. Choose a filename matching `scenarios/scenario_<number>_<description>.yaml`.
3. Create the YAML with only active schema fields:
   - `meta`
   - `operators`
   - `weights`
   - `buses`
4. Avoid inactive legacy fields such as `priority_class` and `charge_strategy`.
5. Preserve original scenarios unless the user explicitly asks to edit them.
6. Validate:

```bash
uv run python scripts/run_scenario.py scenarios/<new-file>.yaml --world-dir world
uv run python -m unittest discover -s tests
```

## Template

Use `assets/scenario_template.yaml` as a starter and remove placeholder comments before finalizing.
