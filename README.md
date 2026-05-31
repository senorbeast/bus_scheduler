# Bus Charging Scheduler

Streamlit app for simulating electric bus charging contention on a Bengaluru-Kochi corridor.
The scheduler uses a deterministic discrete-event simulation with weighted soft rules for
charger queue arbitration.

## Run Locally

This project uses `uv`.

```bash
uv run streamlit run app.py
```

Run tests:

```bash
uv run python -m unittest discover -s tests
```

## Scenarios

- `scenario_1.yaml` through `scenario_5.yaml` are the original full-corridor baseline scenarios.
- `scenario_6_intermediate_ab_ba.yaml` and `scenario_7_mixed_full_and_intermediate.yaml` demonstrate intermediate station-origin trips such as A to B and B to A.

The original scenario intent is preserved. Intermediate-trip behavior is demonstrated through
separate scenario files.

## Deployment

Streamlit Community Cloud supports `uv.lock`, and this repo intentionally uses a single Python
dependency source: `pyproject.toml` plus `uv.lock`.

