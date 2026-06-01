---
name: create-bus-world
description: Create a valid bus scheduler world YAML file. Use when Codex is asked to add a new world/*.yaml file modeled on world/bengaluru_kochi.yaml, including linear route segments, station chargers, physics, planner threshold, and validation through a scenario that references the new world.
---

# Create Bus World

Use this skill to create a new `world/*.yaml` file for the bus scheduler. Current runtime support is
for linear routes only.

## Required Clarifications

Before writing YAML, ask the user if any required data is missing:

- World ID and human-readable name.
- Ordered route nodes and segment distances.
- Which intermediate nodes are charging stations.
- Charger IDs, operational flags, and availability windows.
- Physics values: battery range, fixed charge time, and travel speed.
- Planner threshold: `extra_stop_wait_threshold_minutes`.
- Whether a new scenario should also be created to validate this world.

Defaults if the user does not specify:

- Use `route.type: "linear"`.
- Use one operational charger per station.
- Use charger window `"00:00"` to `"23:59"`.
- Use planner threshold `120`.
- Name the file `world/<snake_case_id>.yaml`, matching the top-level `id`.

## Workflow

1. Inspect `world/bengaluru_kochi.yaml` and the loader's world schema in `scheduler/loader.py`.
2. Create `world/<id>.yaml`.
3. Ensure station IDs appear as route nodes.
4. Do not introduce graph-route schema; current loader accepts only `linear`.
5. If validating with a new scenario, create a minimal scenario referencing `meta.world_id: "<id>"`.
6. Validate:

```bash
uv run python scripts/run_scenario.py scenarios/<scenario-for-world>.yaml --world-dir world
uv run python -m unittest discover -s tests
```

## Template

Use `assets/world_template.yaml` as a starter and remove placeholder comments before finalizing.
