import streamlit as st
from pathlib import Path

from scheduler.loader import load_scenario
from scheduler.planner import get_charging_plan
from scheduler.simulator import Scheduler
from ui.components import render_bus_timetable, render_scenario_input, render_station_views

SCENARIOS_DIR = Path(__file__).parent / "scenarios"

st.set_page_config(page_title="Bus Charging Scheduler", layout="wide")


@st.cache_data(show_spinner="Running simulation...")
def _run(scenario_id: str, weights: dict[str, float]):
    """Load, plan, and simulate. Cached by (scenario_id, weights) so the
    simulation only re-runs when the user changes the scenario or a weight."""
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    scenario = load_scenario(path)
    scenario.weights = weights
    plans = {bus.id: get_charging_plan(bus, scenario) for bus in scenario.buses}
    return Scheduler(scenario, plans).run(), scenario


with st.sidebar:
    scenario = render_scenario_input(SCENARIOS_DIR)

st.title("Bus Charging Scheduler")
st.caption("Bengaluru → A → B → C → D → Kochi  ·  Electric fleet charging planner")

result, sim_scenario = _run(scenario.scenario_id, scenario.weights)

tab_timetable, tab_stations = st.tabs(["Bus Timetable", "Station Views"])

with tab_timetable:
    render_bus_timetable(result, sim_scenario)

with tab_stations:
    render_station_views(result, sim_scenario)
