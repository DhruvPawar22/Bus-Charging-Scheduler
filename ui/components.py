from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pandas as pd
import streamlit as st

from scheduler.loader import load_scenario
from scheduler.models import ScheduleResult, Scenario, StationServiceEntry
from scheduler.simulator import minutes_to_time_str


# ---------------------------------------------------------------------------
# Scenario input panel
# ---------------------------------------------------------------------------

def render_scenario_input(scenarios_dir: Path) -> Scenario:
    """
    Renders scenario picker + weight sliders. Designed to live in the sidebar.
    Returns the selected Scenario with user-adjusted weights applied.
    """
    files = sorted(scenarios_dir.glob("scenario_*.json"))
    if not files:
        st.error(f"No scenario files found in {scenarios_dir}")
        st.stop()

    # Quick-read just the name fields to populate the dropdown — avoids
    # building full model objects for every file on every re-run.
    name_to_file: dict[str, Path] = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            display = data.get("scenario_name", f.stem)
        except Exception:
            display = f.stem
        name_to_file[display] = f

    st.header("Scenario")
    selected_name = st.selectbox(
        "Choose a scenario",
        list(name_to_file.keys()),
        label_visibility="collapsed",
    )
    scenario = load_scenario(name_to_file[selected_name])
    st.caption(scenario.description)

    st.divider()
    st.subheader("Scoring Weights")
    st.caption(
        "Higher weight = rule has more influence on queue priority. "
        "Set a weight to 0 to disable that rule."
    )

    new_weights: dict[str, float] = {}
    for rule_name, default_val in scenario.weights.items():
        new_weights[rule_name] = st.slider(
            label=_weight_label(rule_name),
            min_value=0.0,
            max_value=3.0,
            value=float(default_val),
            step=0.1,
            key=f"weight_{rule_name}",
        )

    scenario.weights = new_weights
    return scenario


def _weight_label(rule_name: str) -> str:
    labels = {
        "individual": "Individual fairness",
        "operator": "Operator fairness",
        "overall": "Overall efficiency (FIFO)",
    }
    return labels.get(rule_name, rule_name.replace("_", " ").title())


# ---------------------------------------------------------------------------
# Bus timetable view
# ---------------------------------------------------------------------------

def render_bus_timetable(result: ScheduleResult, scenario: Scenario) -> None:
    """
    Renders a summary row of aggregate metrics followed by a per-bus table.
    Each bus row shows departure, charging stop details, arrival, and total wait.
    """
    node_names = {n.id: n.name for n in scenario.route.nodes}
    op_names = {oid: op.name for oid, op in scenario.operators.items()}

    all_waits = [
        tl.total_wait_min for tl in result.bus_timelines.values()
    ]
    total_buses = len(all_waits)
    total_wait = sum(all_waits)
    max_wait = max(all_waits) if all_waits else 0.0
    buses_delayed = sum(1 for w in all_waits if w > 0)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total buses", total_buses)
    col2.metric("Buses delayed", buses_delayed)
    col3.metric("Total wait", f"{total_wait:.0f} min")
    col4.metric("Worst delay", f"{max_wait:.0f} min")

    st.divider()

    rows = []
    for bus in scenario.buses:
        tl = result.bus_timelines[bus.id]
        origin_name = node_names.get(bus.origin, bus.origin)
        dest_name = node_names.get(bus.destination, bus.destination)

        charging_summary = " → ".join(
            f"{ev.station_id} (wait {ev.wait_time_min:.0f}m)"
            for ev in tl.charging_events
        ) or "—"

        rows.append({
            "Bus": bus.id,
            "Operator": op_names.get(bus.operator_id, bus.operator_id),
            "Route": f"{origin_name} → {dest_name}",
            "Departure": minutes_to_time_str(tl.departure_time_min),
            "Charging stops": charging_summary,
            "Arrival": minutes_to_time_str(tl.arrival_time_min),
            "Total wait (min)": round(tl.total_wait_min, 1),
            "Journey (min)": round(tl.total_journey_min, 1),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Station detail view
# ---------------------------------------------------------------------------

def render_station_views(result: ScheduleResult, scenario: Scenario) -> None:
    """
    Renders one tab per charging station showing a service log and summary
    metrics (buses served, total wait, peak queue length).
    """
    station_ids = scenario.route.get_charging_station_ids()
    node_names = {n.id: n.name for n in scenario.route.nodes}
    op_names = {oid: op.name for oid, op in scenario.operators.items()}

    tabs = st.tabs([node_names.get(sid, sid) for sid in station_ids])

    for tab, sid in zip(tabs, station_ids):
        with tab:
            log = result.station_logs.get(sid)
            entries = sorted(log.entries, key=lambda e: e.charge_start_min) if log else []

            if not entries:
                st.info("No buses were served at this station in this scenario.")
                continue

            total_wait = sum(e.wait_time_min for e in entries)
            peak_q = _peak_queue_length(entries)

            c1, c2, c3 = st.columns(3)
            c1.metric("Buses served", len(entries))
            c2.metric("Total wait", f"{total_wait:.0f} min")
            c3.metric("Peak queue", peak_q)

            rows = []
            for e in entries:
                rows.append({
                    "Bus": e.bus_id,
                    "Operator": op_names.get(e.operator_id, e.operator_id),
                    "Arrival": minutes_to_time_str(e.arrival_time_min),
                    "Wait (min)": round(e.wait_time_min, 1),
                    "Charge start": minutes_to_time_str(e.charge_start_min),
                    "Charge end": minutes_to_time_str(e.charge_end_min),
                })

            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _peak_queue_length(entries: List[StationServiceEntry]) -> int:
    """Maximum number of buses simultaneously waiting for a charger."""
    events: list[tuple[float, int]] = []
    for e in entries:
        if e.wait_time_min > 0:
            events.append((e.arrival_time_min, +1))
            events.append((e.charge_start_min, -1))
    events.sort()
    peak = current = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak
