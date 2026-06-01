# Bus Charging Scheduler

Streamlit app for simulating electric bus charging contention on a Bengaluru-Kochi corridor.
The scheduler uses a deterministic discrete-event simulation with weighted soft rules for
charger queue arbitration.

## Run Locally

This project uses `uv`.

```bash
uv run streamlit run app.py
```

During active development, run Streamlit with save-triggered reruns and polling:

```bash
uv run streamlit run app.py --server.runOnSave true --server.fileWatcherType poll
```

Code edits rerun the Streamlit script automatically. Adding or editing
`scenarios/scenario_*.yaml` under the repo should also be detected because Streamlit watches the
current working directory and subdirectories. If a brand-new scenario file does not appear
immediately, press Streamlit's rerun button or refresh the browser; the scenario dropdown is rebuilt
by `list_scenarios("scenarios")` on each script run.

Run tests:

```bash
uv run python -m unittest discover -s tests
```

Run a quick syntax/import check:

```bash
uv run python -m compileall app.py scheduler ui tests scripts
```

Run one scenario without Streamlit:

```bash
uv run python scripts/run_scenario.py scenarios/scenario_1.yaml --world-dir world
```

## Scenarios

- `scenario_1.yaml` through `scenario_5.yaml` are the original full-corridor baseline scenarios.
- `scenario_6_intermediate_ab_ba.yaml` and `scenario_7_mixed_full_and_intermediate.yaml` demonstrate intermediate station-origin trips such as A to B and B to A.

The original scenario intent is preserved. Intermediate-trip behavior is demonstrated through
separate scenario files.

Change rule weights by editing a scenario YAML file:

```yaml
weights:
  individual: 2.0
  operator: 1.0
  overall: 0.5
  shift: 0.0
```

To test a fresh scenario, add a new `scenarios/scenario_<number>_<name>.yaml` file and run:

```bash
uv run python scripts/run_scenario.py scenarios/scenario_<number>_<name>.yaml --world-dir world
```

To add a soft queue rule, create a `SoftRule` subclass in `scheduler/rules/soft_rules.py`, register
it in the `rules=[...]` list inside `scheduler.engine.run_simulation()`, and add a matching
defaulted field to `Weights` plus a scenario YAML `weights.<rule_key>` entry. A commented template
lives at the bottom of `scheduler/rules/soft_rules.py`.

## Codex Skills

Project-local Codex skills live under `skills/` for common scheduler changes:

- `add-planner-hard-rule`: add planner-side hard constraints that reject invalid charging plans.
- `add-scorer-soft-rule`: add weighted soft queue-priority rules for charger arbitration.
- `create-bus-scenario`: create valid `scenarios/scenario_*.yaml` files from operating inputs.
- `create-bus-world`: create valid `world/*.yaml` corridor definitions.

The soft-rule skill follows the project convention that every user-facing soft rule gets an explicit
`Weights` field, loader parse entry, scenario YAML `weights.<rule_key>` value, scorer registration,
and tests.

## Current Scope

- One linear world route is implemented: `Bengaluru -> A -> B -> C -> D -> Kochi`.
- En-route charging plans are assigned before simulation and are not revised mid-run.
- Origin charging is supported for station-origin trips with `requires_origin_charge: true`.
- Charger availability windows are enforced during simulation.
- Dynamic charger failures, graph routes, queue capacity, and partial-charge duration remain future work.

## Deployment

Streamlit Community Cloud supports `uv.lock`, and this repo intentionally uses a single Python
dependency source: `pyproject.toml` plus `uv.lock`.
