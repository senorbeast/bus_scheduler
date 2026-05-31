"""Linear RouteProvider implementation."""

from __future__ import annotations

from scheduler.models import RouteProvider, RouteSegment, VALID_DIRECTIONS


class LinearRouteProvider(RouteProvider):
    """Route provider for a single ordered corridor."""

    def __init__(self, segments: list[RouteSegment], station_ids: list[str]) -> None:
        if not segments:
            raise ValueError("LinearRouteProvider requires at least one segment.")
        self._segments = segments
        self._station_ids = station_ids
        self._bk_positions, self._total = self._compute_bk_positions(segments)
        self._kb_positions = {
            node: self._total - distance for node, distance in self._bk_positions.items()
        }
        self._validate_chain()

    def _validate_chain(self) -> None:
        for left, right in zip(self._segments, self._segments[1:]):
            if left.to_node != right.from_node:
                raise ValueError("Route segments must form a contiguous linear chain.")
        for station_id in self._station_ids:
            if station_id not in self._bk_positions:
                raise ValueError(f"Station '{station_id}' is not present in route segments.")

    @staticmethod
    def _compute_bk_positions(
        segments: list[RouteSegment],
    ) -> tuple[dict[str, float], float]:
        positions: dict[str, float] = {}
        distance = 0.0
        for segment in segments:
            positions[segment.from_node] = distance
            distance += segment.distance_km
        positions[segments[-1].to_node] = distance
        return positions, distance

    @property
    def origin(self) -> str:
        return self._segments[0].from_node

    @property
    def destination(self) -> str:
        return self._segments[-1].to_node

    def get_node_positions(self, direction: str) -> dict[str, float]:
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"Invalid direction '{direction}'.")
        return self._bk_positions if direction == "BK" else self._kb_positions

    def get_station_ids(self) -> list[str]:
        return list(self._station_ids)

    def get_total_distance(self) -> float:
        return self._total

    def has_node(self, node_id: str) -> bool:
        return node_id in self._bk_positions

    def get_direction(self, origin_node: str, destination_node: str) -> str:
        self._require_node(origin_node)
        self._require_node(destination_node)
        if origin_node == destination_node:
            raise ValueError("Bus origin_node and destination_node must differ.")
        return (
            "BK"
            if self._bk_positions[destination_node] > self._bk_positions[origin_node]
            else "KB"
        )

    def get_distance_between(
        self, from_node: str, to_node: str, direction: str | None = None
    ) -> float:
        self._require_node(from_node)
        self._require_node(to_node)
        actual_direction = direction or self.get_direction(from_node, to_node)
        positions = self.get_node_positions(actual_direction)
        distance = positions[to_node] - positions[from_node]
        if distance < 0:
            raise ValueError(
                f"Cannot travel from {from_node} to {to_node} in direction {actual_direction}."
            )
        return distance

    def get_station_ids_between(self, origin_node: str, destination_node: str) -> list[str]:
        direction = self.get_direction(origin_node, destination_node)
        positions = self.get_node_positions(direction)
        start = positions[origin_node]
        end = positions[destination_node]
        stations = [
            station_id
            for station_id in self._station_ids
            if start < positions[station_id] < end
        ]
        return sorted(stations, key=lambda station_id: positions[station_id])

    def get_next_reachable_stations(
        self, from_node: str, direction: str, range_km: float
    ) -> list[str]:
        self._require_node(from_node)
        positions = self.get_node_positions(direction)
        from_pos = positions[from_node]
        reachable = [
            (positions[station_id] - from_pos, station_id)
            for station_id in self._station_ids
            if 0 < positions[station_id] - from_pos <= range_km
        ]
        reachable.sort()
        return [station_id for _, station_id in reachable]

    def _require_node(self, node_id: str) -> None:
        if node_id not in self._bk_positions:
            raise ValueError(f"Unknown route node '{node_id}'.")
