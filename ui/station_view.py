"""Station charge-log view."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from scheduler.models import StationChargeLog
from ui.formatting import fmt_time


def render_station_view(logs: list[StationChargeLog]) -> None:
    """Render station logs."""
    for log in logs:
        st.subheader(f"Station {log.station_id}")
        rows = [
            {
                "bus_id": entry["bus_id"],
                "operator": entry["operator"],
                "kind": entry["event_kind"],
                "arrival": fmt_time(entry["arrival_time"]),
                "charge_start": fmt_time(entry["charge_start"]),
                "charge_end": fmt_time(entry["charge_end"]),
                "wait_min": round(entry["wait_time"], 1),
                "charger": entry["charger_id"],
            }
            for entry in log.entries
        ]
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No charging sessions.")

