# Architecture — Bus Charging Scheduler

---

## Scheduling Framework: Event-Driven Simulation with Pluggable Priority Rules

### What it is

The scheduler runs an **event-driven simulation** over a priority queue of timestamped events. The two event types are:

- `bus_arrival` — a bus reaches a charging station
- `charging_complete` — a bus finishes charging and frees the charger

When a bus arrives at a station with a free charger, it starts immediately. When it arrives at a busy station, it joins a queue. When a charger frees up, the scheduler picks the next bus from the queue using a **weighted priority score** computed from pluggable rules.

### Why this approach

| Requirement | Why event-driven simulation fits |
|-------------|----------------------------------|
| Charger contention (1 charger, N buses) | Priority queues are the natural primitive for "who goes next" decisions |
| Tunable weights | Weights are inputs to the scoring function — changing them changes decisions without touching the engine |
| New rules without rewriting | Each rule is an independent object. The scoring loop sums `weight × rule.score()` over whatever rules are registered. Adding a rule is additive, not invasive. |
| Growing world (more buses, stations, routes) | The simulation is O(N log N) in events. Adding buses or stations adds events, not code paths. |
| Correct by construction | Hard constraints (range, charger exclusivity) are enforced structurally — the planner only generates valid plans, and the simulator only assigns one bus per charger slot at a time. |

### What was considered and rejected

**Constraint programming / ILP:** Finds a globally optimal solution but requires reformulating the entire problem whenever a new rule is added. The spec explicitly says rules will keep changing — an approach that forces re-formulation on every change is the wrong fit.

**Greedy heuristics (hand-written):** Fast and simple, but weights end up scattered across conditional logic. "Changing a weight" means finding all the places it's referenced. The spec explicitly rules this out.

**Auction / market-based scheduling:** Theoretically elegant but hard to explain and debug. The event-driven simulation produces an execution trace that maps directly to the per-bus and per-station views the UI needs.

---

## Two-Phase Scheduling

### Phase 1 — Charging Plan (Planner)

Before the simulation runs, each bus is assigned a **charging plan**: an ordered list of stations it will charge at.

**Algorithm: Latest-Safe (Lazy) Charging**

Starting at the bus's origin with a full battery, the planner walks forward along the route. It identifies the last station the bus can charge at before its range would be exhausted, adds it to the plan, resets the range budget, and continues. This produces the minimum number of stops with the latest possible charging points.

**Result for this route:**
- Bengaluru → Kochi buses charge at: **[B, D]**
- Kochi → Bengaluru buses charge at: **[C, A]**

**Why lazy charging?**
- Minimum stops = minimum time spent charging
- Charging as late as possible defers contention, giving the simulation more room to spread buses out
- It mirrors the decision a driver would naturally make
- It is fully derived from the route geometry — no hardcoded station names

### Phase 2 — Contention Resolution (Simulator)

The event-driven simulation resolves who charges when. Charging plans are fixed inputs; the simulation's only job is to determine queue ordering at each station.

**Priority score formula:**

```
score(bus) = w_individual × individual_score(bus)
           + w_operator   × operator_score(bus)
           + w_overall    × overall_score(bus)
```

Higher score → served first.

| Rule | Score definition | Effect when weight is high |
|------|-----------------|---------------------------|
| `individual` | Total wait this bus has accumulated at all previous stations | Buses that have been delayed more get compensated at future stations |
| `operator` | Average total wait across all buses from the same operator | Operators whose fleets are running late get their buses prioritised |
| `overall` | Time this bus has been waiting at the current station | FIFO order — minimises total queue wait (optimal for equal service times) |

All three scores are in **minutes**, making them directly comparable when weights are equal.

---

## Data Structure Design

### Why JSON

- Human-readable and directly editable — the interview task of "encode a new scenario on the spot" is a 5-minute text edit, not a database operation
- Supports the nested structure the problem requires (route graph, per-station config, per-bus properties)
- No dependency beyond Python's stdlib `json` module
- Streamlit Community Cloud has no filesystem restrictions on bundled files

### Schema overview

```
scenario/
  scenario_id          string
  scenario_name        string
  description          string

  world/
    route/
      nodes[]          {id, name, node_type}        ← "terminal" or "charging_station"
      segments[]       {from_node, to_node, distance_km}

    physics/
      battery_range_km
      charge_time_minutes
      speed_kmh

  stations{}           {chargers: int}              ← keyed by station id
  operators{}          {name: string}               ← keyed by operator id
  weights{}            {rule_name: float, ...}      ← flat dict, arbitrary keys
  buses[]              {id, operator, origin, destination, departure_time, ...optional fields}
```

### Key design decisions

**Route as a graph (nodes + segments), not a hardcoded list**

The planner and simulator derive everything from the graph: travel distances, valid charging station sequences, node ordering. Adding a station means adding one node and splitting one segment — the rest of the code adapts automatically.

**`origin` and `destination` on each bus, not `direction: "BK"/"KB"`**

A string enum like "BK" only works for two endpoints. `origin`/`destination` works for any number of terminals, partial routes, or buses that start mid-route.

**`weights` is a flat `Dict[str, float]`, not a fixed dataclass**

Any new rule added to `scheduler/rules.py` can immediately be given a weight by adding one key to the scenario JSON. The scoring loop picks it up automatically — no schema migration, no dataclass field addition.

**`chargers: int` per station, not hardcoded to 1**

The simulator allocates charger slots (0 to chargers-1) per station. Doubling capacity at Station B is a one-character JSON edit.

**Physics constants in the scenario file, not in code**

`battery_range_km`, `charge_time_minutes`, and `speed_kmh` are data, not constants. Changing the battery technology or running the simulation at a different speed requires no code change.

**Optional per-bus field overrides**

Fields like `battery_range_km`, `speed_kmh`, and `priority` can appear on individual bus objects to override world-level defaults. The loader and simulator check bus-level values first, falling back to world-level. This pattern supports mixed fleets without code changes.

---

## Anticipated Future Changes

This table documents every change considered during design and how the current data structure handles it — in each case, without modifying Python code.

| Change | How the design handles it |
|--------|--------------------------|
| **Add a new charging station** | Add a node (`node_type: "charging_station"`) to `world.route.nodes`, split the relevant segment into two in `world.route.segments`, add the station id to `stations{}`. The planner re-derives valid plans from the new geometry. |
| **Remove a charging station** | Delete the node and merge its two segments back into one. |
| **Change a segment distance** | Edit `distance_km` on the relevant segment. Planner and travel-time calculations update automatically. |
| **Double the chargers at a station** | Change `"chargers": 1` to `"chargers": 2` for that station. The simulator allocates two charger slots and runs two buses in parallel. |
| **Add a new operator** | Add `"new_op": {"name": "New Operator"}` to `operators{}`. Assign it to buses. The `OperatorFairnessRule` picks it up automatically. |
| **Add more buses** | Add entries to `buses[]`. No upper limit in the engine. |
| **Fewer buses (asymmetric load)** | Remove entries from `buses[]`. Already demonstrated in Scenario 3 (14 buses). |
| **Change bus speed** | Edit `world.physics.speed_kmh`. All travel times recompute. |
| **Per-bus speed (mixed fleet)** | Add `"speed_kmh": 80` to a bus object. Loader and simulator check bus-level value first. |
| **Different battery size** | Edit `world.physics.battery_range_km`. Planner recomputes valid plans. |
| **Per-bus battery (mixed fleet)** | Add `"battery_range_km": 300` to a bus object. |
| **Different charging duration** | Edit `world.physics.charge_time_minutes`. |
| **Priority buses** | Add `"priority": "high"` to a bus; add `PriorityBusRule` to `rules.py` (5 lines); add `"priority": 5.0` to `weights{}`. |
| **Time-of-day electricity costs** | Add `"cost_schedule": [...]` to a station; add a `CostAwareRule` that factors cost into priority; add `"cost": 1.0` to `weights{}`. |
| **Driver shift constraints** | Add `"driver": {"shift_end": "23:00"}` to a bus; add a `DriverShiftRule` that penalises plans pushing past shift end. |
| **Partial routes** (bus starts mid-route) | Already supported — `origin` and `destination` are arbitrary node IDs, not constrained to terminals. |
| **Multiple routes sharing stations** | Each scenario defines its own route graph. A station can appear in multiple scenarios with different charger counts. Cross-route contention requires listing multiple routes in a scenario's world block — a one-field addition to the schema. |
| **New scenario on the spot** | Create a new `scenario_N.json`. The app discovers all `scenario_*.json` files at startup. |
| **New soft rule** | Add a class to `rules.py` inheriting `ScoringRule` (10 lines), register it in `DEFAULT_RULES`, add its weight key to the scenario JSON. |
| **New hard rule** (e.g., max wait cap) | Add a validation check in the simulator after queue selection. The engine structure accommodates pre/post-selection hooks without rewriting the loop. |
| **Rename an operator** | Edit `"name"` in the `operators{}` block. IDs stay stable; the display name updates everywhere. |
| **Rebalance weights live** | Edit the `weights{}` block in the JSON. Reload the app. No code deployment. |

---

## How to Change a Weight

Open the relevant scenario JSON. Find the `weights` block. Edit the number.

```json
// Before
"weights": { "individual": 1.0, "operator": 1.0, "overall": 1.0 }

// After — operator fairness matters twice as much
"weights": { "individual": 1.0, "operator": 2.0, "overall": 1.0 }
```

The scoring loop in `simulator.py` reads `scenario.weights.get(rule_name, 0.0)` for each rule — it never references weights by name anywhere else in the codebase.

---

## How to Add a New Rule

**Example: Priority Bus rule — VIP buses jump the queue.**

```python
# scheduler/rules.py

# Step 1: Define the rule
class PriorityBusRule(ScoringRule):
    def score(self, bus_id, arrival_time, current_time,
              bus_states, bus_configs, scenario):
        return 1000.0 if bus_configs[bus_id].get("priority") == "high" else 0.0

# Step 2: Register it
DEFAULT_RULES = {
    "individual": IndividualWaitRule(),
    "operator":   OperatorFairnessRule(),
    "overall":    OverallEfficiencyRule(),
    "priority":   PriorityBusRule(),    # ← add this line
}
```

```json
// Step 3: scenario JSON — set weight and flag buses
"weights": { "individual": 1.0, "operator": 1.0, "overall": 1.0, "priority": 5.0 },
"buses": [
  { "id": "bus-BK-01", ..., "priority": "high" }
]
```

The simulator loop is:
```python
score = sum(
    scenario.weights.get(rule_name, 0.0) * rule.score(bus_id, ...)
    for rule_name, rule in DEFAULT_RULES.items()
)
```

Any key absent from `weights` contributes 0.0. Any key absent from `DEFAULT_RULES` is ignored. The two dicts are independently extensible.

---

## Assumptions

| Assumption | Rationale |
|------------|-----------|
| Speed is 60 km/h for all buses | Suggested in the spec. Uniform speed keeps travel times deterministic and comparable across scenarios. |
| Charging always fills to 100% | Stated in the spec. Partial charging is not modelled. |
| All buses depart from a terminal with a full charge | Stated in the spec. Endpoint chargers are excluded from the scheduling problem. |
| Default charging plan is "latest-safe" (lazy) | Minimises stops, defers charging as long as safely possible, derivable purely from route geometry. Defensible as the strategy a driver would naturally use. |
| BK buses charge at [B, D]; KB buses charge at [C, A] | Derived from the lazy algorithm applied to this specific route geometry. These are the only 2-stop plans that charge at the last geometrically safe station at each decision point. |
| Priority score: `individual` = past accumulated waits only (not current wait) | Separates "has this bus been unlucky before" (individual) from "has this bus been waiting here" (overall). Makes the two weights genuinely independent. |
| Priority score: `overall` = current wait at this station | FIFO order at each station. Queueing theory result: for equal service times, FIFO minimises average wait. |
| Priority score: `operator` = mean of total waits across all buses from that operator | A simple fleet-level fairness metric that responds to both fleet size and accumulated delay. |
| Tie-breaking uses a monotonic sequence counter | Ensures deterministic, reproducible output when two buses score identically. Earlier-registered events (lower sequence number) win ties. |
| Times are stored internally as minutes from midnight | Avoids datetime library complexity. Displayed as HH:MM strings in the UI. Supports schedules crossing midnight (e.g., a bus departing 21:00 arrives ~04:50). |
| Scenario 3 intentionally has 14 buses (not 20) | Confirmed in spec: "only 4 going Kochi→Bengaluru". |
| No validation of operator IDs on buses against the operators block | The spec does not require this. A bus with an unrecognised operator simply has no peers for the operator fairness calculation, which degrades gracefully to 0. |
