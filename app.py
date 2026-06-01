"""Streamlit entry point for the bus charging scheduler."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from scheduler.engine import run_simulation
from scheduler.loader import list_scenarios, load_scenario
from scheduler.models import Scenario, SimulationResult
from ui.bus_timetable import render_bus_timetables
from ui.scenario_view import render_scenario_view
from ui.station_view import render_station_view


st.set_page_config(page_title="Bus Charging Scheduler", layout="wide")
st.title("Bus Charging Scheduler")


def data_signature(path: str) -> tuple[tuple[str, int, int], ...]:
    """Return file metadata that should invalidate cached simulation results."""
    files = [Path(path), *sorted(Path("world").glob("*.yaml"))]
    return tuple(
        (str(file_path), file_path.stat().st_mtime_ns, file_path.stat().st_size)
        for file_path in files
        if file_path.exists()
    )


@st.cache_data(show_spinner=False)
def cached_simulation(
    path: str,
    signature: tuple[tuple[str, int, int], ...],
) -> tuple[Scenario, SimulationResult]:
    _ = signature
    scenario = load_scenario(path)
    return scenario, run_simulation(scenario)


scenario_files = list_scenarios("scenarios")
if not scenario_files:
    st.error("No scenario YAML files found in scenarios/.")
    st.stop()

selected_path = st.selectbox(
    "Scenario",
    scenario_files,
    format_func=lambda path: Path(path).stem.replace("_", " ").title(),
)

try:
    selected_path_str = str(selected_path)
    scenario, result = cached_simulation(selected_path_str, data_signature(selected_path_str))
except Exception as exc:  # pragma: no cover - Streamlit display path
    st.exception(exc)
    st.stop()

metric_cols = st.columns(5)
metric_cols[0].metric(
    "Total Wait",
    f"{result.total_network_wait_minutes:.0f} min",
    help="Sum of all bus waiting time before charging starts. It does not include charging or travel time.",
)
metric_cols[1].metric(
    "Max Bus Wait",
    f"{result.max_single_bus_wait_minutes:.0f} min",
    help="Highest total charging wait accumulated by any single bus.",
)
metric_cols[2].metric(
    "Network Duration",
    f"{result.simulation_duration_minutes:.0f} min",
    help="Elapsed time from the earliest scheduled bus departure to the latest simulated bus arrival.",
)
metric_cols[3].metric(
    "Avg Trip Time",
    f"{result.avg_trip_time_minutes:.0f} min",
    help="Average scheduled-departure-to-arrival trip time across all buses in this scenario.",
)
metric_cols[4].metric(
    "Buses",
    str(len(result.bus_timetables)),
    help="Number of bus trips in the selected scenario.",
)

tab1, tab2, tab3 = st.tabs(["Scenario Input", "Bus Timetables", "Station View"])
with tab1:
    render_scenario_view(scenario, result)
with tab2:
    render_bus_timetables(result.bus_timetables)
with tab3:
    render_station_view(result.station_logs)
