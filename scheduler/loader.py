from __future__ import annotations
import json
from pathlib import Path
from typing import Dict

from scheduler.models import (
    BusConfig,
    Operator,
    PhysicsConstants,
    Route,
    RouteNode,
    RouteSegment,
    Scenario,
    StationConfig,
)


def load_scenario(path: str | Path) -> Scenario:
    """
    Read a scenario JSON file and return a fully populated Scenario object.

    Raises:
        FileNotFoundError: if the path does not exist.
        KeyError / ValueError: if required fields are missing or invalid.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    route = _load_route(data["world"]["route"])
    physics = _load_physics(data["world"]["physics"])
    stations = _load_stations(data["stations"])
    operators = _load_operators(data["operators"])
    weights = _load_weights(data.get("weights", {}))
    buses = _load_buses(data["buses"], route)

    return Scenario(
        scenario_id=data["scenario_id"],
        scenario_name=data["scenario_name"],
        description=data.get("description", ""),
        route=route,
        physics=physics,
        stations=stations,
        operators=operators,
        weights=weights,
        buses=buses,
    )


# ---------------------------------------------------------------------------
# Private helpers — one per top-level JSON section
# ---------------------------------------------------------------------------

def _load_route(data: dict) -> Route:
    nodes = [
        RouteNode(
            id=n["id"],
            name=n["name"],
            node_type=n["node_type"],
        )
        for n in data["nodes"]
    ]
    segments = [
        RouteSegment(
            from_node=s["from_node"],
            to_node=s["to_node"],
            distance_km=float(s["distance_km"]),
        )
        for s in data["segments"]
    ]
    return Route(nodes=nodes, segments=segments)


def _load_physics(data: dict) -> PhysicsConstants:
    return PhysicsConstants(
        battery_range_km=float(data["battery_range_km"]),
        charge_time_minutes=float(data["charge_time_minutes"]),
        speed_kmh=float(data["speed_kmh"]),
    )


def _load_stations(data: dict) -> Dict[str, StationConfig]:
    return {
        sid: StationConfig(id=sid, chargers=int(cfg.get("chargers", 1)))
        for sid, cfg in data.items()
    }


def _load_operators(data: dict) -> Dict[str, Operator]:
    return {
        oid: Operator(id=oid, name=cfg.get("name", oid))
        for oid, cfg in data.items()
    }


def _load_weights(data: dict) -> Dict[str, float]:
    return {key: float(val) for key, val in data.items()}


def _load_buses(data: list, route: Route) -> list[BusConfig]:
    valid_node_ids = {n.id for n in route.nodes}
    buses = []

    for b in data:
        origin = b["origin"]
        destination = b["destination"]

        if origin not in valid_node_ids:
            raise ValueError(
                f"Bus '{b['id']}' has unknown origin '{origin}'. "
                f"Valid nodes: {sorted(valid_node_ids)}"
            )
        if destination not in valid_node_ids:
            raise ValueError(
                f"Bus '{b['id']}' has unknown destination '{destination}'. "
                f"Valid nodes: {sorted(valid_node_ids)}"
            )

        # Pull out known fields; anything extra goes into .extra for forward compatibility
        known = {"id", "operator", "origin", "destination", "departure_time",
                 "battery_range_km", "speed_kmh", "priority"}
        extra = {k: v for k, v in b.items() if k not in known}

        buses.append(BusConfig(
            id=b["id"],
            operator_id=b["operator"],
            origin=origin,
            destination=destination,
            departure_time=b["departure_time"],
            battery_range_km=b.get("battery_range_km"),
            speed_kmh=b.get("speed_kmh"),
            priority=b.get("priority"),
            extra=extra,
        ))

    return buses
