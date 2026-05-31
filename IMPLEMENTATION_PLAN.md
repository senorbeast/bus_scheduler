# IMPLEMENTATION_PLAN.md

## Goal
Implement the Streamlit bus charging scheduler described in the project markdown files, preserving original scenario intent and adding separate intermediate-trip scenarios.

## Progress
- [x] Scaffold package, UI, world, scenario, and test directories.
- [x] Configure `uv` project metadata and lock dependencies for Streamlit deployment.
- [x] Implement typed scheduler models and route abstraction.
- [x] Implement linear route provider.
- [x] Implement YAML loader with world/scenario split.
- [x] Implement planner for full and intermediate endpoint trips.
- [x] Implement hard rules, soft rules, and weighted scorer.
- [x] Implement deterministic discrete-event simulation engine.
- [x] Harden charger event tracking and charger-window queue rechecks.
- [x] Implement Streamlit UI views.
- [x] Add world YAML and original five scenario files.
- [x] Add separate intermediate-trip scenarios.
- [x] Add tests.
- [x] Update `OVERVIEW.md` with complexity notes.
- [x] Update `REFUTE.md` and `FUTURE_CHANGES.md` with implementation findings.
- [x] Run verification commands and mark complete.
- [x] Re-run verification after continuation hardening.

## Implementation Boundary
- V1 supports one linear physical route with arbitrary origin/destination nodes on that route.
- Dependencies are managed by `pyproject.toml` and `uv.lock`; do not add a second deployment dependency file unless switching package managers.
- Static charging plans are used for en-route charging.
- Origin charging can create contention for short station-origin trips.
- Graph routing, dynamic charger failure re-routing, and multi-route shared-station networks remain future work.
