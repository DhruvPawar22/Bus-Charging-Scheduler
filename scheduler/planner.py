from __future__ import annotations
from typing import List

from scheduler.models import BusConfig, Scenario


def get_charging_plan(bus: BusConfig, scenario: Scenario) -> List[str]:
    """
    Determine which charging stations a bus visits, in travel order.

    Strategy: Latest-Safe (lazy) charging.
      - Charge at the LAST possible station before the battery would run out.
      - This produces the minimum number of stops and defers charging as long
        as safely possible — mirroring what a driver would naturally do.

    Expected results for this route (Bengaluru→A→B→C→D→Kochi, range 240 km):
      - Bengaluru → Kochi : ["B", "D"]
      - Kochi → Bengaluru : ["C", "A"]

    The algorithm is fully derived from the route graph and physics constants —
    no station names are hardcoded.

    Args:
        bus:      The bus to plan for (origin, destination, optional range override).
        scenario: Full scenario (route graph, physics).

    Returns:
        Ordered list of station IDs the bus must charge at.

    Raises:
        ValueError: if the route is physically infeasible (a single segment
                    exceeds the battery range — a data error, not a scheduling one).
    """
    route = scenario.route
    battery_range = scenario.effective_range(bus)

    # Build the ordered list of nodes from origin to destination
    node_order = route.get_node_order()
    origin_idx = node_order.index(bus.origin)
    dest_idx = node_order.index(bus.destination)

    if origin_idx < dest_idx:
        travel_nodes = node_order[origin_idx: dest_idx + 1]
    else:
        travel_nodes = list(reversed(node_order[dest_idx: origin_idx + 1]))

    # Precompute cumulative distances from the origin along the travel path
    cum_distances = _cumulative_distances(travel_nodes, route)

    # Charging station IDs along this bus's travel path (excluding endpoints)
    station_ids = {sid for sid in scenario.stations}
    charging_stops = [
        (travel_nodes[i], cum_distances[i])
        for i in range(1, len(travel_nodes) - 1)
        if travel_nodes[i] in station_ids
    ]

    # --- Latest-Safe algorithm ---
    # Walk forward. When we can't reach the next waypoint from the last charge
    # point, insert the most-recently-passed charging station into the plan.

    plan: List[str] = []
    last_charge_cum = 0.0          # cumulative distance where battery was last full
    last_seen_station: tuple | None = None   # (station_id, cum_dist) — most recent station passed

    all_waypoints = charging_stops + [(travel_nodes[-1], cum_distances[-1])]

    for station_id, cum_dist in all_waypoints:
        dist_since_last_charge = cum_dist - last_charge_cum

        if dist_since_last_charge > battery_range:
            # Can't reach this waypoint — must have charged at the last station we passed
            if last_seen_station is None:
                raise ValueError(
                    f"Bus '{bus.id}': cannot reach '{station_id}' "
                    f"({dist_since_last_charge:.0f} km) within range "
                    f"({battery_range:.0f} km) and no prior charging station exists. "
                    f"Check segment distances."
                )

            prev_id, prev_cum = last_seen_station
            plan.append(prev_id)
            last_charge_cum = prev_cum
            last_seen_station = None   # consumed — don't re-add on next iteration

            # Re-check: can we reach this waypoint from the newly added charge point?
            dist_since_last_charge = cum_dist - last_charge_cum
            if dist_since_last_charge > battery_range:
                raise ValueError(
                    f"Bus '{bus.id}': segment from '{prev_id}' to '{station_id}' "
                    f"is {dist_since_last_charge:.0f} km, exceeding battery range "
                    f"of {battery_range:.0f} km. Route is infeasible."
                )

        # If this waypoint is a charging station, remember it as the latest candidate
        if station_id in station_ids:
            last_seen_station = (station_id, cum_dist)

    return plan


# ---------------------------------------------------------------------------
# Private helper
# ---------------------------------------------------------------------------

def _cumulative_distances(travel_nodes: List[str], route) -> List[float]:
    """
    Returns cumulative distances from travel_nodes[0] for each node.
    Index 0 is always 0.0 (the origin).
    """
    cum = [0.0]
    for i in range(len(travel_nodes) - 1):
        seg_dist = route.distance_between(travel_nodes[i], travel_nodes[i + 1])
        cum.append(cum[-1] + seg_dist)
    return cum
