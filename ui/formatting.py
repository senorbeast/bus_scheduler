"""UI formatting helpers."""

from __future__ import annotations

from scheduler.models import minutes_to_time_str


def fmt_time(minutes: float) -> str:
    """Format simulation minutes for display."""
    return minutes_to_time_str(minutes)

