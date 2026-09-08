"""Explainable, human-reviewable load suggestions for Dispatch."""

from __future__ import annotations

from typing import Any

from strands import tool


def _available_lbs(item: dict[str, Any]) -> float | None:
    quantity = item.get("quantity")
    if quantity is None:
        return None
    unit = str(item.get("unit", "")).strip().lower()
    if unit in {"lb", "lbs", "pound", "pounds"}:
        return max(float(quantity), 0.0)
    unit_weight = item.get("unit_weight_lbs")
    if unit_weight is None:
        return None
    return max(float(quantity), 0.0) * max(float(unit_weight), 0.0)


def _lot_statuses(cold_chain: list[dict[str, Any]]) -> dict[str, str]:
    statuses = {}
    for lot in cold_chain:
        status = str(lot.get("status", "")).strip().lower().replace("_", " ")
        for key in (lot.get("item_id"), lot.get("item_name"), lot.get("lot_id")):
            if key:
                statuses[str(key).strip().lower()] = status
    return statuses


@tool
def recommend_load(
    route: dict,
    inventory: list,
    cold_chain: list,
    vehicle_capacity_lbs: float,
    requested_load_lbs: float,
) -> dict:
    """Suggest a safe load without exceeding on-hand stock or vehicle capacity.

    Quantities in pounds are used directly. Case/item quantities require an
    explicit ``unit_weight_lbs`` conversion; Dispatch never invents one. Lots
    marked At risk or Hold are excluded until a person clears them.
    """
    capacity = max(float(vehicle_capacity_lbs), 0.0)
    target = min(max(float(requested_load_lbs), 0.0), capacity)
    status_by_key = _lot_statuses(cold_chain)
    candidates, excluded = [], []
    for item in inventory:
        keys = [item.get("item_id"), item.get("item_name")]
        status = next(
            (status_by_key[str(key).strip().lower()] for key in keys if key and str(key).strip().lower() in status_by_key),
            "ok",
        )
        pounds = _available_lbs(item)
        if status in {"at risk", "hold"}:
            excluded.append({
                "item_id": item.get("item_id"),
                "item": item.get("item_name"),
                "reason": f"Cold-chain status is {status.title()}; human clearance required.",
            })
            continue
        if pounds is None:
            excluded.append({
                "item_id": item.get("item_id"),
                "item": item.get("item_name"),
                "reason": "No pounds conversion is available; set unit_weight_lbs before load planning.",
            })
            continue
        if pounds > 0:
            candidates.append((0 if item.get("category") == "cold-chain" else 1, item, pounds))

    candidates.sort(key=lambda value: (value[0], str(value[1].get("item_name", ""))))
    remaining = target
    suggested = []
    for _priority, item, available in candidates:
        assigned = min(available, remaining)
        if assigned <= 0:
            break
        suggested.append({
            "item_id": item.get("item_id"),
            "item": item.get("item_name"),
            "category": item.get("category"),
            "assigned_lbs": round(assigned, 1),
            "available_lbs": round(available, 1),
            "reason": "Available on hand and cleared for loading; cold-chain-safe items are allocated first.",
        })
        remaining -= assigned

    stops = route.get("selected_stops") or []
    demand_total = sum(max(float(stop.get("demand", 0)), 0) for stop in stops)
    allocated_lbs = round(target - remaining, 1)
    stop_allocations = []
    for stop in stops:
        share = (max(float(stop.get("demand", 0)), 0) / demand_total) if demand_total else (1 / len(stops) if stops else 0)
        stop_allocations.append({
            "stop_id": stop.get("stop_id"),
            "tract_id": stop.get("tract_fips"),
            "allocated_lbs": round(allocated_lbs * share, 1),
            "basis": "proportional_to_planned_stop_demand",
        })

    return {
        "requested_load_lbs": round(float(requested_load_lbs), 1),
        "vehicle_capacity_lbs": round(capacity, 1),
        "assigned_load_lbs": allocated_lbs,
        "reserve_lbs": round(capacity - allocated_lbs, 1),
        "unfilled_request_lbs": round(remaining, 1),
        "items": suggested,
        "stop_allocations": stop_allocations,
        "excluded_items": excluded,
        "requires_human_review": True,
    }
