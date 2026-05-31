"""Scenario summary view."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from scheduler.models import Scenario, SimulationResult


def render_scenario_view(scenario: Scenario, result: SimulationResult) -> None:
    """Render scenario configuration and aggregate results."""
    st.subheader(scenario.meta.get("name", scenario.meta.get("id", "Scenario")))
    st.caption(scenario.meta.get("description", ""))

    st.write("Rule weights")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "individual": scenario.weights.individual,
                    "operator": scenario.weights.operator,
                    "overall": scenario.weights.overall,
                    "shift": scenario.weights.shift,
                }
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.write("Buses")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "bus_id": bus.id,
                    "operator": bus.operator,
                    "departure": bus.departure,
                    "origin": bus.origin_node,
                    "destination": bus.destination_node,
                    "direction": bus.direction,
                    "requires_origin_charge": bus.requires_origin_charge,
                    "initial_range_km": bus.initial_range_km,
                    "weight": bus.weight,
                }
                for bus in scenario.buses
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.write("Average wait by operator")
    st.dataframe(
        pd.DataFrame(
            [
                {"operator": operator, "avg_wait_min": wait}
                for operator, wait in sorted(result.per_operator_avg_wait.items())
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

