# Bus Charging Scheduler

A Python + Streamlit app that schedules electric bus charging along the Bengaluru–Kochi route. The scheduler decides which stations each bus charges at and resolves contention when multiple buses compete for the same charger — using a pluggable, weight-driven priority system.

---

## Running Locally

**Prerequisites:** Python 3.10+

```bash
# 1. Clone the repo
git clone https://github.com/<your-handle>/Bus-Charging-Scheduler.git
cd Bus-Charging-Scheduler

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Launch the app
streamlit run app.py
# Opens at http://localhost:8501
```

---

## Project Structure

```
Bus-Charging-Scheduler/
├── app.py                    # Streamlit entry point (~40 lines, wires everything together)
├── requirements.txt
├── README.md
├── ARCHITECTURE.md
│
├── scheduler/                # Pure Python — zero Streamlit imports
│   ├── models.py             # All dataclasses (Scenario, BusConfig, ScheduleResult, ...)
│   ├── loader.py             # Reads a scenario JSON → returns a Scenario object
│   ├── planner.py            # Phase 1: assigns charging plan per bus (lazy/latest-safe)
│   ├── simulator.py          # Phase 2: event-driven simulation + priority queue
│   └── rules.py              # Pluggable scoring rules (individual, operator, overall)
│
├── scenarios/                # One JSON file per scenario — these ARE the data model
│   ├── scenario_1.json       # Even spacing — baseline
│   ├── scenario_2.json       # Bunched start — heavy early contention
│   ├── scenario_3.json       # Asymmetric load — 10 BK, 4 KB
│   ├── scenario_4.json       # Operator-heavy KPN, operator weight = 2.0
│   └── scenario_5.json       # Worst case — all 20 buses in 72-minute window
│
└── ui/
    └── components.py         # Streamlit rendering functions (no scheduling logic)
```

---

## How to Change a Weight

Weights live in exactly one place: the `"weights"` block of each scenario's JSON file.

**Example — boosting operator fairness in Scenario 1:**

```json
// scenarios/scenario_1.json
"weights": {
  "individual": 1.0,
  "operator":   2.0,   // was 1.0 — KPN/Freshbus/Flixbus fleets now get more equalisation
  "overall":    1.0
}
```

Reload the app. No code changes required.

**What each weight controls:**

| Weight | Effect when increased |
|--------|-----------------------|
| `individual` | Buses that have already been delayed at earlier stations get priority at subsequent stations |
| `operator` | Buses from the operator whose fleet is most behind schedule get priority |
| `overall` | Earlier-arriving buses at a station get priority (FIFO — minimises total queue wait) |

---

## How to Add a New Rule

Three steps. No engine changes.

**Example — Priority Bus rule (VIP buses skip the queue):**

### Step 1 — Define the rule in `scheduler/rules.py`

```python
class PriorityBusRule(ScoringRule):
    """Gives a large score bonus to buses marked as high-priority."""
    def score(self, bus_id, arrival_time, current_time,
              bus_states, bus_configs, scenario):
        return 1000.0 if bus_configs[bus_id].get("priority") == "high" else 0.0
```

### Step 2 — Register it in `DEFAULT_RULES` (same file, bottom)

```python
DEFAULT_RULES = {
    "individual": IndividualWaitRule(),
    "operator":   OperatorFairnessRule(),
    "overall":    OverallEfficiencyRule(),
    "priority":   PriorityBusRule(),       # ← new line
}
```

### Step 3 — Set the weight and flag buses in the scenario JSON

```json
"weights": {
  "individual": 1.0,
  "operator":   1.0,
  "overall":    1.0,
  "priority":   5.0
},

"buses": [
  {
    "id": "bus-BK-01",
    "operator": "kpn",
    "origin": "bengaluru",
    "destination": "kochi",
    "departure_time": "19:00",
    "priority": "high"        // ← this bus now jumps the queue
  }
]
```

That's it. The scoring loop in the simulator automatically picks up any key present in both `DEFAULT_RULES` and the scenario's `weights` dict.

---

## How to Add a New Scenario

Create a new JSON file in `scenarios/` following the same schema as the existing files. The app discovers all `scenario_*.json` files at startup — no code change needed.

The minimum viable scenario file:

```json
{
  "scenario_id": "scenario_6",
  "scenario_name": "Scenario 6 — My Custom Test",
  "description": "Whatever you're testing.",

  "world": {
    "route": {
      "nodes": [ ... ],
      "segments": [ ... ]
    },
    "physics": {
      "battery_range_km": 240,
      "charge_time_minutes": 25,
      "speed_kmh": 60
    }
  },

  "stations": { "A": {"chargers": 1}, "B": {"chargers": 1}, "C": {"chargers": 1}, "D": {"chargers": 1} },
  "operators": { "kpn": {"name": "KPN"}, "freshbus": {"name": "Freshbus"}, "flixbus": {"name": "Flixbus"} },
  "weights":   { "individual": 1.0, "operator": 1.0, "overall": 1.0 },

  "buses": [ ... ]
}
```

---

## Deployment (Streamlit Community Cloud)

1. Push the repo to GitHub (must be public)
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
3. Select repo, set **Main file path** to `app.py`
4. Click **Deploy** — Streamlit reads `requirements.txt` and builds automatically

No environment variables, no secrets, no build configuration required.

---

## Assumptions

Full list in [ARCHITECTURE.md](ARCHITECTURE.md#assumptions).
