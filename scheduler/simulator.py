from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from scheduler.models import (
    BusConfig,
    BusTimeline,
    ChargingEventResult,
    ScheduleResult,
    Scenario,
    StationLog,
    StationServiceEntry,
)
from scheduler.rules import DEFAULT_RULES


# ---------------------------------------------------------------------------
# Time utilities
# ---------------------------------------------------------------------------

def time_str_to_minutes(time_str: str) -> float:
    h, m = time_str.split(":")
    return int(h) * 60 + int(m)


def minutes_to_time_str(minutes: float) -> str:
    total = int(round(minutes))
    return f"{total // 60:02d}:{total % 60:02d}"


# ---------------------------------------------------------------------------
# Internal simulation types
# ---------------------------------------------------------------------------

@dataclass(order=True)
class _Event:
    time: float
    # charging_complete=0 fires before bus_arrival=1 at the same time tick.
    # This ensures queued buses are served before a same-instant arrival claims
    # the charger, which would silently double-book a single-charger station.
    type_priority: int
    seq: int
    event_type: str = field(compare=False)
    bus_id: str = field(compare=False)
    station_id: str = field(compare=False)
    charger_idx: int = field(compare=False)


@dataclass
class _BusState:
    remaining_stations: List[str]
    total_wait_min: float
    charging_events: List[ChargingEventResult]
    departure_time_min: float
    arrival_at_current: float   # arrival time at the station currently being visited
    arrival_time_min: float     # final destination arrival (set when journey completes)


@dataclass
class _StationState:
    station_id: str
    charger_free_at: Dict[int, float]       # charger index → time it becomes free
    queue: List[Tuple[float, str]]          # (arrival_time, bus_id) awaiting a charger
    service_log: List[StationServiceEntry]


# ---------------------------------------------------------------------------
# Priority selection
# ---------------------------------------------------------------------------

def _select_by_priority(
    queue: List[Tuple[float, str]],
    current_time: float,
    bus_states: Dict[str, _BusState],
    bus_configs: Dict[str, BusConfig],
    scenario: Scenario,
) -> str:
    best_id = queue[0][1]
    best_score = float("-inf")
    for arrival_time, bus_id in queue:
        score = sum(
            scenario.weights.get(name, 0.0) * rule.score(
                bus_id, arrival_time, current_time, bus_states, bus_configs, scenario
            )
            for name, rule in DEFAULT_RULES.items()
        )
        if score > best_score:
            best_score = score
            best_id = bus_id
    return best_id


# ---------------------------------------------------------------------------
# Scheduler — public API
# ---------------------------------------------------------------------------

class Scheduler:
    """
    Two-phase bus charging scheduler.

    Phase 1 (planner): each bus gets a minimal charging plan (latest-safe stops).
    Phase 2 (this class): event-driven simulation resolves charger contention
    using pluggable, weighted priority rules.

    Usage:
        plans = {bus.id: get_charging_plan(bus, scenario) for bus in scenario.buses}
        result = Scheduler(scenario, plans).run()
    """

    def __init__(self, scenario: Scenario, charging_plans: Dict[str, List[str]]):
        self.scenario = scenario
        self.charging_plans = charging_plans

    def run(self) -> ScheduleResult:
        scenario = self.scenario
        route = scenario.route
        physics = scenario.physics
        bus_configs: Dict[str, BusConfig] = {b.id: b for b in scenario.buses}

        # ── Bus states ───────────────────────────────────────────────────────
        bus_states: Dict[str, _BusState] = {}
        for bus in scenario.buses:
            plan = self.charging_plans.get(bus.id, [])
            dep = time_str_to_minutes(bus.departure_time)
            bus_states[bus.id] = _BusState(
                remaining_stations=plan.copy(),
                total_wait_min=0.0,
                charging_events=[],
                departure_time_min=dep,
                arrival_at_current=0.0,
                arrival_time_min=0.0,
            )

        # ── Station states ───────────────────────────────────────────────────
        station_states: Dict[str, _StationState] = {
            sid: _StationState(
                station_id=sid,
                charger_free_at={i: 0.0 for i in range(scfg.chargers)},
                queue=[],
                service_log=[],
            )
            for sid, scfg in scenario.stations.items()
        }

        # ── Seed the event heap ──────────────────────────────────────────────
        heap: List[_Event] = []
        seq = 0

        for bus in scenario.buses:
            state = bus_states[bus.id]
            speed = scenario.effective_speed(bus)
            if state.remaining_stations:
                first_station = state.remaining_stations[0]
                dist = route.distance_between(bus.origin, first_station)
                arrival = state.departure_time_min + dist / speed * 60.0
                heapq.heappush(heap, _Event(
                    time=arrival, type_priority=1, seq=seq,
                    event_type="bus_arrival",
                    bus_id=bus.id, station_id=first_station, charger_idx=-1,
                ))
                seq += 1
            else:
                # No charging needed — direct journey
                dist = route.distance_between(bus.origin, bus.destination)
                state.arrival_time_min = state.departure_time_min + dist / speed * 60.0

        # ── Event loop ───────────────────────────────────────────────────────
        while heap:
            ev = heapq.heappop(heap)
            bus = bus_configs[ev.bus_id]
            state = bus_states[ev.bus_id]
            station = station_states[ev.station_id]
            speed = scenario.effective_speed(bus)

            if ev.event_type == "bus_arrival":
                state.remaining_stations.pop(0)
                state.arrival_at_current = ev.time

                charger_idx = self._free_charger(station, ev.time)
                if charger_idx is not None:
                    seq = self._start_charging(
                        charger_idx, ev.bus_id, ev.time, ev.station_id,
                        station, heap, seq, physics.charge_time_minutes,
                    )
                else:
                    station.queue.append((ev.time, ev.bus_id))

            elif ev.event_type == "charging_complete":
                charge_end = ev.time
                charge_start = charge_end - physics.charge_time_minutes
                wait_time = charge_start - state.arrival_at_current

                state.total_wait_min += wait_time
                state.charging_events.append(ChargingEventResult(
                    station_id=ev.station_id,
                    arrival_time_min=state.arrival_at_current,
                    wait_time_min=wait_time,
                    charge_start_min=charge_start,
                    charge_end_min=charge_end,
                ))
                station.service_log.append(StationServiceEntry(
                    bus_id=ev.bus_id,
                    operator_id=bus.operator_id,
                    arrival_time_min=state.arrival_at_current,
                    wait_time_min=wait_time,
                    charge_start_min=charge_start,
                    charge_end_min=charge_end,
                ))

                # Schedule the bus's next leg
                if state.remaining_stations:
                    next_station = state.remaining_stations[0]
                    dist = route.distance_between(ev.station_id, next_station)
                    next_arrival = charge_end + dist / speed * 60.0
                    heapq.heappush(heap, _Event(
                        time=next_arrival, type_priority=1, seq=seq,
                        event_type="bus_arrival",
                        bus_id=ev.bus_id, station_id=next_station, charger_idx=-1,
                    ))
                    seq += 1
                else:
                    dist = route.distance_between(ev.station_id, bus.destination)
                    state.arrival_time_min = charge_end + dist / speed * 60.0

                # Serve the highest-priority queued bus on the now-free charger
                if station.queue:
                    next_bus_id = _select_by_priority(
                        station.queue, ev.time, bus_states, bus_configs, scenario
                    )
                    i = next(
                        j for j, (_, bid) in enumerate(station.queue)
                        if bid == next_bus_id
                    )
                    station.queue.pop(i)
                    seq = self._start_charging(
                        ev.charger_idx, next_bus_id, ev.time, ev.station_id,
                        station, heap, seq, physics.charge_time_minutes,
                    )

        # ── Build result ─────────────────────────────────────────────────────
        bus_timelines = {
            bus.id: BusTimeline(
                bus_id=bus.id,
                operator_id=bus.operator_id,
                origin=bus.origin,
                destination=bus.destination,
                departure_time_min=bus_states[bus.id].departure_time_min,
                charging_events=bus_states[bus.id].charging_events,
                arrival_time_min=bus_states[bus.id].arrival_time_min,
            )
            for bus in scenario.buses
        }
        station_logs = {
            sid: StationLog(station_id=sid, entries=ss.service_log)
            for sid, ss in station_states.items()
        }
        return ScheduleResult(
            scenario_id=scenario.scenario_id,
            scenario_name=scenario.scenario_name,
            bus_timelines=bus_timelines,
            station_logs=station_logs,
        )

    @staticmethod
    def _free_charger(station: _StationState, current_time: float) -> Optional[int]:
        for idx, free_at in station.charger_free_at.items():
            if free_at <= current_time:
                return idx
        return None

    @staticmethod
    def _start_charging(
        charger_idx: int,
        bus_id: str,
        charge_start: float,
        station_id: str,
        station: _StationState,
        heap: List[_Event],
        seq: int,
        charge_time: float,
    ) -> int:
        charge_end = charge_start + charge_time
        station.charger_free_at[charger_idx] = charge_end
        heapq.heappush(heap, _Event(
            time=charge_end, type_priority=0, seq=seq,
            event_type="charging_complete",
            bus_id=bus_id, station_id=station_id, charger_idx=charger_idx,
        ))
        return seq + 1
