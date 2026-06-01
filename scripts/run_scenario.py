"""Run one scenario from the command line without Streamlit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scheduler.engine import run_simulation
from scheduler.loader import load_scenario
from scheduler.models import minutes_to_time_str


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load a scenario YAML file, run the scheduler, and print the result."
    )
    parser.add_argument("scenario", help="Path to a scenarios/scenario_*.yaml file.")
    parser.add_argument(
        "--world-dir",
        default="world",
        help="Directory containing world YAML files. Defaults to world.",
    )
    args = parser.parse_args()

    scenario_path = Path(args.scenario)
    scenario = load_scenario(str(scenario_path), world_dir=args.world_dir)
    result = run_simulation(scenario)

    print(f"Scenario: {result.scenario_id}")
    print(f"Buses: {len(result.bus_timetables)}")
    print(f"Total wait: {result.total_network_wait_minutes:.1f} min")
    print(f"Max single-bus wait: {result.max_single_bus_wait_minutes:.1f} min")
    print(f"Simulation duration: {result.simulation_duration_minutes:.1f} min")
    print(f"Average trip time: {result.avg_trip_time_minutes:.1f} min")
    print("Per-operator average wait:")
    for operator, wait in sorted(result.per_operator_avg_wait.items()):
        print(f"  {operator}: {wait:.1f} min")

    print("\nBus timetables:")
    for timetable in result.bus_timetables:
        plan = " -> ".join(timetable.charging_plan) if timetable.charging_plan else "(none)"
        print(
            "  "
            f"{timetable.bus_id} "
            f"{timetable.origin_node}->{timetable.destination_node} "
            f"dep={minutes_to_time_str(timetable.departure_time)} "
            f"route_dep={minutes_to_time_str(timetable.route_departure_time)} "
            f"arr={minutes_to_time_str(timetable.arrival_time)} "
            f"wait={timetable.total_wait_time:.1f} "
            f"plan={plan}"
        )
        for event in timetable.charging_events:
            print(
                "    "
                f"{event.station_id}/{event.charger_id} "
                f"{event.event_kind} "
                f"arr={minutes_to_time_str(event.arrival_time)} "
                f"start={minutes_to_time_str(event.charge_start)} "
                f"end={minutes_to_time_str(event.charge_end)} "
                f"wait={event.wait_time:.1f}"
            )

    print("\nStation logs:")
    for log in result.station_logs:
        print(f"  {log.station_id}:")
        if not log.entries:
            print("    (no charging sessions)")
            continue
        for entry in log.entries:
            print(
                "    "
                f"{entry['bus_id']} "
                f"{entry['event_kind']} "
                f"{entry['charger_id']} "
                f"arr={minutes_to_time_str(entry['arrival_time'])} "
                f"start={minutes_to_time_str(entry['charge_start'])} "
                f"end={minutes_to_time_str(entry['charge_end'])} "
                f"wait={entry['wait_time']:.1f}"
            )


if __name__ == "__main__":
    main()
