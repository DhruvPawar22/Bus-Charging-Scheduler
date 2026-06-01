from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Input models — represent a loaded scenario
# ---------------------------------------------------------------------------

@dataclass
class PhysicsConstants:
    battery_range_km: float
    charge_time_minutes: float
    speed_kmh: float


@dataclass
class RouteNode:
    id: str
    name: str
    node_type: str  # "terminal" | "charging_station"


@dataclass
class RouteSegment:
    from_node: str
    to_node: str
    distance_km: float


@dataclass
class Route:
    nodes: List[RouteNode]
    segments: List[RouteSegment]

    def get_node_order(self) -> List[str]:
        """Node IDs in forward route order, derived from the segment chain."""
        if not self.segments:
            return []
        order = [self.segments[0].from_node]
        for seg in self.segments:
            order.append(seg.to_node)
        return order

    def distance_between(self, from_id: str, to_id: str) -> float:
        """
        Sum of segment distances between two nodes.
        Direction-agnostic: distance(A, B) == distance(B, A).
        """
        node_order = self.get_node_order()
        fi = node_order.index(from_id)
        ti = node_order.index(to_id)

        if fi == ti:
            return 0.0
        if fi > ti:
            fi, ti = ti, fi

        total = 0.0
        for seg in self.segments:
            si = node_order.index(seg.from_node)
            ei = node_order.index(seg.to_node)
            if si > ei:
                si, ei = ei, si
            if si >= fi and ei <= ti:
                total += seg.distance_km
        return total

    def get_charging_station_ids(self) -> List[str]:
        """All charging station IDs in forward route order."""
        order = self.get_node_order()
        station_ids = {n.id for n in self.nodes if n.node_type == "charging_station"}
        return [n for n in order if n in station_ids]


@dataclass
class StationConfig:
    id: str
    chargers: int = 1


@dataclass
class Operator:
    id: str
    name: str


@dataclass
class BusConfig:
    id: str
    operator_id: str
    origin: str
    destination: str
    departure_time: str          # "HH:MM"
    # Optional per-bus overrides — if absent, world-level physics apply
    battery_range_km: Optional[float] = None
    speed_kmh: Optional[float] = None
    priority: Optional[str] = None
    # Holds any extra fields from JSON without breaking the loader
    extra: Dict = field(default_factory=dict)


@dataclass
class Scenario:
    scenario_id: str
    scenario_name: str
    description: str
    route: Route
    physics: PhysicsConstants
    stations: Dict[str, StationConfig]   # station_id → config
    operators: Dict[str, Operator]       # operator_id → config
    weights: Dict[str, float]            # rule_name → weight
    buses: List[BusConfig]

    def effective_range(self, bus: BusConfig) -> float:
        """Battery range for a specific bus, respecting per-bus overrides."""
        return bus.battery_range_km if bus.battery_range_km is not None \
            else self.physics.battery_range_km

    def effective_speed(self, bus: BusConfig) -> float:
        """Travel speed for a specific bus, respecting per-bus overrides."""
        return bus.speed_kmh if bus.speed_kmh is not None \
            else self.physics.speed_kmh


# ---------------------------------------------------------------------------
# Output models — produced by the simulator
# ---------------------------------------------------------------------------

@dataclass
class ChargingEventResult:
    station_id: str
    arrival_time_min: float
    wait_time_min: float
    charge_start_min: float
    charge_end_min: float


@dataclass
class BusTimeline:
    bus_id: str
    operator_id: str
    origin: str
    destination: str
    departure_time_min: float
    charging_events: List[ChargingEventResult]
    arrival_time_min: float

    @property
    def total_wait_min(self) -> float:
        return sum(e.wait_time_min for e in self.charging_events)

    @property
    def total_journey_min(self) -> float:
        return self.arrival_time_min - self.departure_time_min


@dataclass
class StationServiceEntry:
    bus_id: str
    operator_id: str
    arrival_time_min: float
    wait_time_min: float
    charge_start_min: float
    charge_end_min: float


@dataclass
class StationLog:
    station_id: str
    entries: List[StationServiceEntry] = field(default_factory=list)


@dataclass
class ScheduleResult:
    scenario_id: str
    scenario_name: str
    bus_timelines: Dict[str, BusTimeline]   # bus_id → timeline
    station_logs: Dict[str, StationLog]     # station_id → log
