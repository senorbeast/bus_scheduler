"""Abstract base classes for scheduler rules."""

from __future__ import annotations

from abc import ABC, abstractmethod

from scheduler.models import BusState, RoutePositions, ScheduleContext


class SoftRule(ABC):
    """Priority scoring rule used during queue arbitration."""

    name = "unnamed_soft_rule"

    @abstractmethod
    def score(self, bus_state: BusState, context: ScheduleContext) -> float:
        """Return a priority score. Higher means charge sooner."""


class HardRule(ABC):
    """Binary plan-validation rule."""

    name = "unnamed_hard_rule"

    @abstractmethod
    def is_satisfied(
        self, stations: list[str], rp: RoutePositions, battery_range: float
    ) -> bool:
        """Return whether the candidate plan satisfies the rule."""

