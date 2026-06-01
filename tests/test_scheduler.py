"""Behavior tests for the bus scheduler."""

from __future__ import annotations

import unittest
from pathlib import Path

from scheduler.engine import run_simulation
from scheduler.loader import load_scenario
from scheduler.models import (
    Bus,
    BusState,
    Charger,
    ChargerState,
    OperatorConfig,
    Physics,
    RouteSegment,
    Scenario,
    ScheduleContext,
    Station,
    StationState,
    Weights,
    minutes_to_time_str,
    time_str_to_minutes,
    driver_shift_end_minutes,
    DriverShift,
    PlannerConfig,
)
from scheduler.planner import assign_charging_plans, get_valid_charging_plans
from scheduler.rules.base import SoftRule
from scheduler.routes.linear import LinearRouteProvider
from scheduler.scoring import WeightedScorer


ROOT = Path(__file__).resolve().parents[1]


def make_scenario(
    segments: list[RouteSegment],
    station_ids: list[str],
    buses: list[Bus],
    battery_range_km: float,
    charge_time_minutes: int = 25,
    extra_stop_wait_threshold_minutes: float = 120.0,
) -> Scenario:
    route = LinearRouteProvider(segments, station_ids)
    return Scenario(
        meta={"id": "test", "world_id": "test"},
        route=route,
        physics=Physics(
            battery_range_km=battery_range_km,
            charge_time_minutes=charge_time_minutes,
            travel_speed_kmh=60.0,
        ),
        planner=PlannerConfig(
            extra_stop_wait_threshold_minutes=extra_stop_wait_threshold_minutes
        ),
        stations=[
            Station(
                id=station_id,
                chargers=(
                    Charger(
                        id=f"{station_id}-1",
                        operational=True,
                        available_from="00:00",
                        available_until="23:59",
                    ),
                ),
            )
            for station_id in station_ids
        ],
        operators=[OperatorConfig("op")],
        weights=Weights(),
        buses=buses,
    )


class ConstantRule(SoftRule):
    """Test-only soft rule with a fixed score."""

    name = "constant"

    def __init__(self, value: float) -> None:
        self.value = value

    def score(self, bus_state, context) -> float:
        return self.value


class TimeUtilityTests(unittest.TestCase):
    def test_time_conversion_handles_next_day(self) -> None:
        self.assertEqual(time_str_to_minutes("19:00"), 1140.0)
        self.assertEqual(minutes_to_time_str(1820.0), "+1d 06:20")

    def test_driver_shift_can_cross_midnight(self) -> None:
        self.assertEqual(driver_shift_end_minutes(DriverShift("17:00", "01:00")), 1500.0)


class RouteAndPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = make_scenario(
            segments=[
                RouteSegment("Bengaluru", "A", 100.0),
                RouteSegment("A", "B", 120.0),
                RouteSegment("B", "C", 100.0),
                RouteSegment("C", "D", 120.0),
                RouteSegment("D", "Kochi", 100.0),
            ],
            station_ids=["A", "B", "C", "D"],
            battery_range_km=240.0,
            buses=[
                Bus("bus-BK", "op", "19:00", "Bengaluru", "Kochi", direction="BK"),
                Bus("bus-KB", "op", "19:00", "Kochi", "Bengaluru", direction="KB"),
            ],
        )

    def test_route_positions_for_both_directions(self) -> None:
        route = self.scenario.route
        self.assertEqual(route.get_node_positions("BK")["A"], 100.0)
        self.assertEqual(route.get_node_positions("KB")["D"], 100.0)
        self.assertEqual(route.get_distance_between("A", "B"), 120.0)
        self.assertEqual(route.get_direction("B", "A"), "KB")

    def test_full_corridor_minimum_plans_match_reference(self) -> None:
        bk_bus = self.scenario.buses[0]
        kb_bus = next(bus for bus in self.scenario.buses if bus.direction == "KB")
        bk_min = {
            tuple(plan)
            for plan in get_valid_charging_plans(bk_bus, self.scenario)
            if len(plan) == 2
        }
        kb_min = {
            tuple(plan)
            for plan in get_valid_charging_plans(kb_bus, self.scenario)
            if len(plan) == 2
        }
        self.assertEqual(bk_min, {("A", "C"), ("B", "C"), ("B", "D")})
        self.assertEqual(kb_min, {("D", "B"), ("C", "B"), ("C", "A")})

    def test_intermediate_trip_needs_no_enroute_charge(self) -> None:
        bus = Bus(
            id="bus-test",
            operator="kpn",
            departure="19:00",
            origin_node="A",
            destination_node="B",
            direction="BK",
        )
        self.assertIn([], get_valid_charging_plans(bus, self.scenario))

    def test_assign_plans_uses_predicted_station_arrival_order(self) -> None:
        scenario = make_scenario(
            segments=[
                RouteSegment("O", "A", 100.0),
                RouteSegment("A", "Q", 120.0),
                RouteSegment("Q", "D", 120.0),
            ],
            station_ids=["A", "Q"],
            battery_range_km=220.0,
            buses=[
                Bus("early-far", "op", "19:00", "O", "D"),
                Bus("later-near", "op", "20:00", "A", "D"),
            ],
        )

        result = run_simulation(scenario)
        q_log = next(log for log in result.station_logs if log.station_id == "Q")

        self.assertEqual(q_log.entries[0]["bus_id"], "later-near")
        self.assertEqual(result.total_network_wait_minutes, 0.0)

    def test_assign_plans_lookahead_avoids_next_bus_collision(self) -> None:
        scenario = make_scenario(
            segments=[
                RouteSegment("X", "O", 20.0),
                RouteSegment("O", "P", 100.0),
                RouteSegment("P", "Q", 20.0),
                RouteSegment("Q", "D", 100.0),
            ],
            station_ids=["P", "Q"],
            battery_range_km=120.0,
            buses=[
                Bus("flex", "op", "19:00", "O", "D"),
                Bus("p-only", "op", "18:50", "X", "Q"),
            ],
        )

        plans = assign_charging_plans(scenario)

        self.assertEqual(plans["flex"], ["Q"])
        self.assertEqual(plans["p-only"], ["P"])

    def test_assign_plans_lookahead_does_not_override_own_wait(self) -> None:
        scenario = make_scenario(
            segments=[
                RouteSegment("X", "O", 60.0),
                RouteSegment("O", "A", 50.0),
                RouteSegment("A", "B", 20.0),
                RouteSegment("B", "D", 70.0),
                RouteSegment("D", "E", 40.0),
            ],
            station_ids=["A", "B"],
            battery_range_km=120.0,
            buses=[
                Bus("block-b", "op", "18:40", "O", "E"),
                Bus("flex", "op", "19:00", "O", "D"),
                Bus("a-only", "op", "18:10", "X", "D"),
            ],
        )

        plans = assign_charging_plans(scenario)

        self.assertEqual(plans["block-b"], ["B"])
        self.assertEqual(plans["flex"], ["A"])
        self.assertEqual(plans["a-only"], ["A"])

    def test_assign_plans_uses_extra_stop_when_minimum_stop_wait_is_excessive(self) -> None:
        buses = [
            Bus(f"block-{index}", "op", "19:00", "O", "D")
            for index in range(5)
        ]
        buses.append(Bus("relief", "op", "19:00", "O", "D"))
        scenario = make_scenario(
            segments=[
                RouteSegment("O", "A", 100.0),
                RouteSegment("A", "B", 100.0),
                RouteSegment("B", "C", 100.0),
                RouteSegment("C", "D", 100.0),
            ],
            station_ids=["A", "B", "C"],
            battery_range_km=200.0,
            buses=buses,
        )

        plans = assign_charging_plans(scenario)

        self.assertTrue(all(plans[f"block-{index}"] == ["B"] for index in range(5)))
        self.assertEqual(plans["relief"], ["A", "C"])

    def test_assign_plans_uses_configured_extra_stop_threshold(self) -> None:
        buses = [
            Bus(f"block-{index}", "op", "19:00", "O", "D")
            for index in range(5)
        ]
        buses.append(Bus("relief", "op", "19:00", "O", "D"))
        scenario = make_scenario(
            segments=[
                RouteSegment("O", "A", 100.0),
                RouteSegment("A", "B", 100.0),
                RouteSegment("B", "C", 100.0),
                RouteSegment("C", "D", 100.0),
            ],
            station_ids=["A", "B", "C"],
            battery_range_km=200.0,
            buses=buses,
            extra_stop_wait_threshold_minutes=200.0,
        )

        plans = assign_charging_plans(scenario)

        self.assertTrue(all(plans[bus.id] == ["B"] for bus in buses))


class ScoringTests(unittest.TestCase):
    def test_weighted_scorer_normalizes_configured_weights(self) -> None:
        scenario = make_scenario(
            segments=[RouteSegment("O", "D", 100.0)],
            station_ids=[],
            battery_range_km=120.0,
            buses=[Bus("bus", "op", "19:00", "O", "D", weight=2.0)],
        )
        bus = scenario.buses[0]
        bus_state = BusState(
            bus=bus,
            charging_plan=[],
            current_range_km=120.0,
            current_time=0.0,
            position="O",
            direction="BK",
        )
        context = ScheduleContext(
            scenario=scenario,
            bus_states={bus.id: bus_state},
            station_states={},
            current_time=0.0,
        )
        scorer = WeightedScorer(
            weights=Weights(individual=3.0, operator=1.0, overall=0.0, shift=0.0),
            rules=[
                (ConstantRule(10.0), "individual"),
                (ConstantRule(30.0), "operator"),
                (ConstantRule(999.0), "overall"),
            ],
        )

        self.assertEqual(scorer.score(bus_state, context), 30.0)

    def test_weighted_scorer_returns_zero_when_all_weights_are_zero(self) -> None:
        scenario = make_scenario(
            segments=[RouteSegment("O", "D", 100.0)],
            station_ids=[],
            battery_range_km=120.0,
            buses=[Bus("bus", "op", "19:00", "O", "D")],
        )
        bus = scenario.buses[0]
        bus_state = BusState(
            bus=bus,
            charging_plan=[],
            current_range_km=120.0,
            current_time=0.0,
            position="O",
            direction="BK",
        )
        context = ScheduleContext(
            scenario=scenario,
            bus_states={bus.id: bus_state},
            station_states={},
            current_time=0.0,
        )
        scorer = WeightedScorer(
            weights=Weights(individual=0.0, operator=0.0, overall=0.0, shift=0.0),
            rules=[(ConstantRule(10.0), "individual")],
        )

        self.assertEqual(scorer.score(bus_state, context), 0.0)


class SimulationTests(unittest.TestCase):
    def test_original_scenarios_load_and_complete(self) -> None:
        for number in range(1, 6):
            with self.subTest(scenario=number):
                scenario = load_scenario(
                    str(ROOT / "scenarios" / f"scenario_{number}.yaml"),
                    str(ROOT / "world"),
                )
                self.assertEqual(scenario.planner.extra_stop_wait_threshold_minutes, 120.0)
                result = run_simulation(scenario)
                self.assertEqual(len(result.bus_timetables), len(scenario.buses))
                self.assertTrue(all(t.arrival_time >= t.departure_time for t in result.bus_timetables))
                for station_log in result.station_logs:
                    starts = [entry["charge_start"] for entry in station_log.entries]
                    self.assertEqual(starts, sorted(starts))

    def test_worst_case_scenario_has_nonzero_wait(self) -> None:
        scenario = load_scenario(str(ROOT / "scenarios" / "scenario_5.yaml"), str(ROOT / "world"))
        result = run_simulation(scenario)
        self.assertGreater(result.total_network_wait_minutes, 0.0)

    def test_intermediate_origin_charging_creates_contention(self) -> None:
        scenario = load_scenario(
            str(ROOT / "scenarios" / "scenario_6_intermediate_ab_ba.yaml"),
            str(ROOT / "world"),
        )
        result = run_simulation(scenario)
        self.assertEqual(len(result.bus_timetables), 4)
        self.assertGreater(result.total_network_wait_minutes, 0.0)
        for timetable in result.bus_timetables:
            self.assertEqual(timetable.charging_plan, [])
            self.assertEqual(len(timetable.charging_events), 1)
            self.assertEqual(timetable.charging_events[0].event_kind, "origin")


class ChargerStateTests(unittest.TestCase):
    def test_charger_window_next_available_time(self) -> None:
        charger = ChargerState(
            charger_id="A-1",
            available_from=time_str_to_minutes("06:00"),
            available_until=time_str_to_minutes("22:00"),
            free_at=0.0,
        )
        self.assertFalse(charger.can_charge_at(time_str_to_minutes("23:00")))
        self.assertEqual(charger.next_available_time(time_str_to_minutes("23:00")), 1800.0)

    def test_station_selects_currently_free_charger(self) -> None:
        station = StationState(
            station_id="A",
            charger_states=[
                ChargerState("A-1", free_at=100.0),
                ChargerState("A-2", free_at=0.0),
            ],
        )
        self.assertEqual(station.get_free_charger_at(50.0).charger_id, "A-2")


if __name__ == "__main__":
    unittest.main()
