from pathlib import Path

import pytest

from scheduler.loader import load_scenario
from scheduler.planner import get_charging_plan
from scheduler.simulator import Scheduler, minutes_to_time_str, time_str_to_minutes

SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(filename: str):
    scenario = load_scenario(SCENARIOS_DIR / filename)
    plans = {bus.id: get_charging_plan(bus, scenario) for bus in scenario.buses}
    result = Scheduler(scenario, plans).run()
    return result, scenario


ALL_SCENARIOS = pytest.mark.parametrize("filename", [
    "scenario_1.json",
    "scenario_2.json",
    "scenario_3.json",
    "scenario_4.json",
    "scenario_5.json",
])


# ---------------------------------------------------------------------------
# Time utilities
# ---------------------------------------------------------------------------

def test_time_str_to_minutes():
    assert time_str_to_minutes("00:00") == 0.0
    assert time_str_to_minutes("19:00") == 1140.0
    assert time_str_to_minutes("23:59") == 1439.0


def test_minutes_to_time_str():
    assert minutes_to_time_str(0.0) == "00:00"
    assert minutes_to_time_str(1140.0) == "19:00"
    assert minutes_to_time_str(91.0) == "01:31"


# ---------------------------------------------------------------------------
# Core invariants — must hold across every scenario
# ---------------------------------------------------------------------------

@ALL_SCENARIOS
def test_all_buses_complete(filename):
    result, scenario = _run(filename)
    for bus in scenario.buses:
        tl = result.bus_timelines[bus.id]
        assert tl.arrival_time_min > tl.departure_time_min, (
            f"{bus.id}: arrival ({tl.arrival_time_min:.1f}) not after "
            f"departure ({tl.departure_time_min:.1f})"
        )


@ALL_SCENARIOS
def test_no_negative_waits(filename):
    result, scenario = _run(filename)
    for bus_id, tl in result.bus_timelines.items():
        for ev in tl.charging_events:
            assert ev.wait_time_min >= -1e-9, (
                f"{bus_id} at {ev.station_id}: negative wait {ev.wait_time_min:.3f} min"
            )


@ALL_SCENARIOS
def test_charge_duration_correct(filename):
    result, scenario = _run(filename)
    expected = scenario.physics.charge_time_minutes
    for bus_id, tl in result.bus_timelines.items():
        for ev in tl.charging_events:
            actual = ev.charge_end_min - ev.charge_start_min
            assert abs(actual - expected) < 1e-9, (
                f"{bus_id} at {ev.station_id}: charge duration {actual:.3f} != {expected}"
            )


@ALL_SCENARIOS
def test_charge_start_not_before_arrival(filename):
    result, scenario = _run(filename)
    for bus_id, tl in result.bus_timelines.items():
        for ev in tl.charging_events:
            assert ev.charge_start_min >= ev.arrival_time_min - 1e-9, (
                f"{bus_id} at {ev.station_id}: charge started before arrival"
            )


@ALL_SCENARIOS
def test_no_charger_overlap_single_charger_stations(filename):
    """At 1-charger stations, service intervals must be strictly sequential."""
    result, scenario = _run(filename)
    for sid, log in result.station_logs.items():
        if scenario.stations[sid].chargers != 1:
            continue
        entries = sorted(log.entries, key=lambda e: e.charge_start_min)
        for i in range(len(entries) - 1):
            a, b = entries[i], entries[i + 1]
            assert b.charge_start_min >= a.charge_end_min - 1e-9, (
                f"Station {sid}: overlap — {a.bus_id} ends {a.charge_end_min:.1f}, "
                f"{b.bus_id} starts {b.charge_start_min:.1f}"
            )


@ALL_SCENARIOS
def test_station_log_bus_count_matches_timelines(filename):
    """Every bus that has a charging event at a station must appear in that station's log."""
    result, scenario = _run(filename)
    for bus_id, tl in result.bus_timelines.items():
        for ev in tl.charging_events:
            log_ids = [e.bus_id for e in result.station_logs[ev.station_id].entries]
            assert bus_id in log_ids, (
                f"{bus_id} has a charging event at {ev.station_id} "
                f"but is missing from the station log"
            )


# ---------------------------------------------------------------------------
# Scenario-specific assertions
# ---------------------------------------------------------------------------

def test_scenario1_correct_stations_visited():
    """BK buses charge at [B, D]; KB buses at [C, A]."""
    result, scenario = _run("scenario_1.json")
    for bus in scenario.buses:
        tl = result.bus_timelines[bus.id]
        visited = [ev.station_id for ev in tl.charging_events]
        if bus.origin == "bengaluru":
            assert visited == ["B", "D"], f"{bus.id}: expected [B, D], got {visited}"
        else:
            assert visited == ["C", "A"], f"{bus.id}: expected [C, A], got {visited}"


def test_scenario1_bk02_waits_at_b():
    """
    BK-01 (19:00) arrives B at 1360, charges 1360–1385.
    BK-02 (19:15) arrives B at 1375, must wait 10 min.
    """
    result, _ = _run("scenario_1.json")
    bk01 = result.bus_timelines["bus-BK-01"]
    bk02 = result.bus_timelines["bus-BK-02"]

    assert bk01.charging_events[0].station_id == "B"
    assert bk01.charging_events[0].wait_time_min == pytest.approx(0.0)

    assert bk02.charging_events[0].station_id == "B"
    assert bk02.charging_events[0].wait_time_min == pytest.approx(10.0)


def test_scenario3_all_kb_buses_complete():
    """Scenario 3: 4 KB buses must complete despite 10 BK buses on the same route."""
    result, scenario = _run("scenario_3.json")
    kb_buses = [b for b in scenario.buses if b.origin == "kochi"]
    assert len(kb_buses) == 4
    for bus in kb_buses:
        tl = result.bus_timelines[bus.id]
        assert len(tl.charging_events) == 2, f"{bus.id}: expected 2 stops, got {len(tl.charging_events)}"
        assert tl.arrival_time_min > tl.departure_time_min


def test_scenario5_has_waits():
    """
    Scenario 5 packs all 20 buses in 72 min — heavy contention.
    At least one bus must wait at a charging station.
    """
    result, _ = _run("scenario_5.json")
    total_wait = sum(
        ev.wait_time_min
        for tl in result.bus_timelines.values()
        for ev in tl.charging_events
    )
    assert total_wait > 0, "Expected some waiting in worst-case scenario"


def test_result_has_correct_scenario_metadata():
    result, scenario = _run("scenario_1.json")
    assert result.scenario_id == scenario.scenario_id
    assert result.scenario_name == scenario.scenario_name
