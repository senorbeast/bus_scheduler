# Repository Guidelines

## Project Structure & Module Organization

This repository implements a Streamlit bus scheduling simulator.

- `app.py` is the Streamlit entry point.
- `scheduler/` contains domain logic: models, YAML loading, route planning, simulation engine, scoring, and rules.
- `scheduler/routes/` holds route providers; `scheduler/rules/` holds hard and soft scheduling rules.
- `ui/` contains Streamlit rendering helpers for scenario summaries, bus timetables, and station views.
- `world/` stores corridor/world definitions such as `bengaluru_kochi.yaml`.
- `scenarios/` stores input scenario YAML files. Keep original scenarios unchanged unless explicitly requested; add new cases as new files.
- `tests/` contains automated tests, currently using Python `unittest`.
- Planning and rationale live in `PLAN.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, `OVERVIEW.md`, `REFUTE.md`, and `FUTURE_CHANGES.md`.

## Build, Test, and Development Commands

Use `uv` for dependency management.

```bash
uv sync
uv run streamlit run app.py
uv run python -m unittest discover -s tests
uv run python -m compileall app.py scheduler ui tests
```

- `uv sync` installs dependencies from `pyproject.toml` and `uv.lock`.
- `uv run streamlit run app.py` starts the local app.
- `unittest discover` runs the scheduler test suite.
- `compileall` catches syntax/import issues quickly.

## Coding Style & Naming Conventions

Use Python 3.11+ with type hints for public functions and dataclasses. Prefer small, focused modules over large procedural files. Keep comments short and only where they clarify non-obvious scheduling or simulation behavior.

Naming conventions:

- Modules and functions: `snake_case`
- Classes and dataclasses: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Scenario files: `scenario_<number>_<optional_description>.yaml`

Avoid ad hoc string parsing for structured data; use typed models and YAML loaders.

## Testing Guidelines

Add or update tests in `tests/test_scheduler.py` when changing scheduling behavior, loader validation, route handling, charger contention, or scoring. Test names should describe the expected behavior, for example `test_intermediate_origin_contention_records_wait_time`.

Run the full test suite before handing off changes:

```bash
uv run python -m unittest discover -s tests
```

## Commit & Pull Request Guidelines

Existing history uses concise conventional-style commits such as `feat: implement bus scheduling engine core with YAML scenario and world loading support`. Prefer `feat:`, `fix:`, `test:`, `docs:`, and `refactor:` prefixes.

Pull requests should include:

- A short summary of user-facing or simulation behavior changes.
- Tests run and their results.
- Notes for changes to scenarios, `REFUTE.md`, `FUTURE_CHANGES.md`, or `OVERVIEW.md`.
- Screenshots when Streamlit UI behavior changes.

## Agent-Specific Instructions

Preserve the original scenario intent. Add new real-world variants as new scenario files and document limitations or rejected assumptions in `REFUTE.md`. Document future operational improvements in `FUTURE_CHANGES.md`.
