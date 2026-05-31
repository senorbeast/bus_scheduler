"""Bus timetable view."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from scheduler.models import BusTimetable
from ui.formatting import fmt_time


def render_bus_timetables(timetables: list[BusTimetable]) -> None:
    """Render per-bus timetable rows."""
    rows = []
    for timetable in timetables:
        charges = "; ".join(
            (
                f"{event.station_id} ({event.event_kind}, "
                f"{fmt_time(event.charge_start)}-{fmt_time(event.charge_end)}, "
                f"wait {event.wait_time:.0f})"
            )
            for event in timetable.charging_events
        )
        rows.append(
            {
                "bus_id": timetable.bus_id,
                "operator": timetable.operator,
                "origin": timetable.origin_node,
                "destination": timetable.destination_node,
                "direction": timetable.direction,
                "scheduled_departure": fmt_time(timetable.departure_time),
                "route_departure": fmt_time(timetable.route_departure_time),
                "arrival": fmt_time(timetable.arrival_time),
                "trip_time_min": round(timetable.total_trip_time, 1),
                "wait_min": round(timetable.total_wait_time, 1),
                "plan": " -> ".join(timetable.charging_plan),
                "charges": charges,
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

