"""Behavior tests for the bus scheduler."""

from __future__ import annotations

import unittest
from pathlib import Path

from scheduler.engine import run_simulation
from scheduler.loader import load_scenario
from scheduler.models import (
    Bus,
    Charger,
    ChargerState,
    StationState,
    minutes_to_time_str,
    time_str_to_minutes,
    driver_shift_end_minutes,
    DriverShift,
)
from scheduler.planner import get_valid_charging_plans


ROOT = Path(__file__).resolve().parents[1]


class TimeUtilityTests(unittest.TestCase):
    def test_time_conversion_handles_next_day(self) -> None:
        self.assertEqual(time_str_to_minutes("19:00"), 1140.0)
        self.assertEqual(minutes_to_time_str(1820.0), "+1d 06:20")

    def test_driver_shift_can_cross_midnight(self) -> None:
        self.assertEqual(driver_shift_end_minutes(DriverShift("17:00", "01:00")), 1500.0)


class RouteAndPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = load_scenario(str(ROOT / "scenarios" / "scenario_1.yaml"), str(ROOT / "world"))

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


class SimulationTests(unittest.TestCase):
    def test_original_scenarios_load_and_complete(self) -> None:
        for number in range(1, 6):
            with self.subTest(scenario=number):
                scenario = load_scenario(
                    str(ROOT / "scenarios" / f"scenario_{number}.yaml"),
                    str(ROOT / "world"),
                )
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
