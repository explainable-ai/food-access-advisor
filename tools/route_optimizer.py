"""Deterministic constrained route optimization for rural food service.

Uses exact subset dynamic programming for up to 15 candidate stops. This is
operations research, not an LLM or predictive model. Callers may provide a
road-network travel-time matrix (preferred); otherwise the result is clearly
labelled as a haversine-based estimate.
"""

from math import isfinite

from strands import tool

from tools.geo import haversine_miles

MAX_CANDIDATES = 15


def _validate(candidates, depot, max_route_minutes, vehicle_capacity, max_stops, service_minutes):
    if not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise ValueError(f"candidate count must be between 1 and {MAX_CANDIDATES}")
    if max_route_minutes <= 0 or vehicle_capacity <= 0 or max_stops <= 0:
        raise ValueError("route time, vehicle capacity, and maximum stops must be positive")
    if service_minutes < 0:
        raise ValueError("service_minutes cannot be negative")
    for name, point in [("depot", depot), *[(f"candidate {i}", row) for i, row in enumerate(candidates)]]:
        if point.get("lat") is None or point.get("lon") is None:
            raise ValueError(f"{name} requires lat and lon")
    ids = [str(row.get("stop_id", "")) for row in candidates]
    if any(not stop_id for stop_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("candidate stop_id values must be non-empty and unique")


def _estimated_matrix(depot, candidates, average_speed_mph):
    if average_speed_mph <= 0:
        raise ValueError("average_speed_mph must be positive")
    points = [depot, *candidates]
    return [[0.0 if i == j else haversine_miles(a["lat"], a["lon"], b["lat"], b["lon"]) /
             average_speed_mph * 60 for j, b in enumerate(points)] for i, a in enumerate(points)]


def _validate_matrix(matrix, size):
    if len(matrix) != size or any(len(row) != size for row in matrix):
        raise ValueError(f"travel_time_matrix must be {size}x{size}, including the depot")
    converted = []
    for row in matrix:
        values = [float(value) for value in row]
        if any(value < 0 or not isfinite(value) for value in values):
            raise ValueError("travel times must be finite and non-negative")
        converted.append(values)
    return converted


def _subset_totals(candidates):
    size = 1 << len(candidates)
    demand, benefit, required = [0.0] * size, [0.0] * size, 0
    for i, candidate in enumerate(candidates):
        if candidate.get("required"):
            required |= 1 << i
    for mask in range(1, size):
        bit = mask & -mask
        index = bit.bit_length() - 1
        previous = mask ^ bit
        stop_demand = max(float(candidates[index].get("demand", 0)), 0)
        priority = max(float(candidates[index].get("need_score", 0)), 0)
        demand[mask] = demand[previous] + stop_demand
        benefit[mask] = benefit[previous] + priority * stop_demand
    return demand, benefit, required


@tool
def optimize_route(candidates: list, depot: dict, max_route_minutes: float,
                   vehicle_capacity: float, max_stops: int,
                   service_minutes: float = 20, travel_time_matrix: list | None = None,
                   average_speed_mph: float = 35) -> dict:
    """Select and sequence stops that maximize priority-weighted service.

    `travel_time_matrix` is indexed `[depot, candidate 0, candidate 1, ...]`.
    Candidate fields: stop_id, lat, lon, demand, need_score, population,
    currently_served, and optional required.
    """
    _validate(candidates, depot, max_route_minutes, vehicle_capacity, max_stops, service_minutes)
    matrix = (_validate_matrix(travel_time_matrix, len(candidates) + 1)
              if travel_time_matrix is not None else _estimated_matrix(depot, candidates, average_speed_mph))
    source = "provided_road_network_matrix" if travel_time_matrix is not None else "haversine_drive_time_estimate"
    demand, benefit, required_mask = _subset_totals(candidates)

    # (visited-mask, last-candidate-index) -> shortest travel time from depot.
    dp, parent = {}, {}
    for i in range(len(candidates)):
        mask = 1 << i
        dp[(mask, i)] = matrix[0][i + 1]
        parent[(mask, i)] = None
    for mask in range(1, 1 << len(candidates)):
        if mask.bit_count() >= max_stops:
            continue
        for last in range(len(candidates)):
            current = dp.get((mask, last))
            if current is None:
                continue
            for nxt in range(len(candidates)):
                bit = 1 << nxt
                if mask & bit:
                    continue
                new_mask = mask | bit
                value = current + matrix[last + 1][nxt + 1]
                if value < dp.get((new_mask, nxt), float("inf")):
                    dp[(new_mask, nxt)] = value
                    parent[(new_mask, nxt)] = (mask, last)

    best = None
    for (mask, last), outbound in dp.items():
        if mask & required_mask != required_mask or demand[mask] > vehicle_capacity:
            continue
        duration = outbound + matrix[last + 1][0] + service_minutes * mask.bit_count()
        if duration > max_route_minutes:
            continue
        candidate = (benefit[mask], demand[mask], -duration, mask, last, duration)
        if best is None or candidate[:3] > best[:3]:
            best = candidate
    if best is None:
        return {"status": "infeasible", "reason": "No route satisfies required stops and constraints",
                "travel_time_source": source, "selected_stops": [], "unselected_stops": [c["stop_id"] for c in candidates]}

    _, served, _, mask, last, duration = best
    order = []
    state = (mask, last)
    while state is not None:
        order.append(state[1])
        state = parent[state]
    order.reverse()
    selected_indexes = set(order)
    selected = [{**candidates[i], "sequence": sequence} for sequence, i in enumerate(order, 1)]
    unselected = [{**candidate, "reason": "not_selected_under_constraints"}
                  for i, candidate in enumerate(candidates) if i not in selected_indexes]
    gains = [row["stop_id"] for row in selected if not row.get("currently_served")]
    losses = [row["stop_id"] for row in unselected if row.get("currently_served")]
    still_uncovered = [row["stop_id"] for row in unselected if not row.get("currently_served")]
    return {"status": "optimal", "objective": "maximize priority-weighted demand served",
            "travel_time_source": source, "route_minutes": round(duration, 1),
            "travel_minutes": round(duration - service_minutes * len(selected), 1),
            "service_minutes": round(service_minutes * len(selected), 1),
            "capacity_used": round(served, 1), "capacity_remaining": round(vehicle_capacity - served, 1),
            "priority_benefit": round(best[0], 1), "selected_stops": selected,
            "unselected_stops": unselected, "coverage_change": {"gained": gains, "lost": losses,
            "still_uncovered": still_uncovered},
            "constraints": {"max_route_minutes": max_route_minutes, "vehicle_capacity": vehicle_capacity,
            "max_stops": max_stops, "service_minutes_per_stop": service_minutes}}
