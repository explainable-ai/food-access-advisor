"""Explainable, human-reviewable load suggestions for Dispatch."""

from __future__ import annotations

from math import floor
from typing import Any

from strands import tool


RISK_ORDER = {"critical": 0, "high": 1, "watch": 2, "medium": 3, "low": 4, "none": 5}


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


def _available_quantity(item: dict[str, Any]) -> float:
    for key in ("on_hand", "quantity", "qty"):
        if item.get(key) is not None:
            return max(float(item[key]), 0.0)
    return 0.0


def _unit_weight(item: dict[str, Any]) -> float:
    return max(float(item.get("unit_weight_lbs", item.get("weight_lbs", 1))), 0.01)


def _risk(item: dict[str, Any]) -> str:
    return str(item.get("cold_chain_risk") or item.get("risk_status") or "none").strip().lower()


def _quantity_allocations(stops: list[dict[str, Any]], quantity: int) -> list[dict[str, Any]]:
    if not stops or quantity <= 0:
        return []
    weights = [max(float(stop.get("households") or stop.get("demand") or 0), 0) for stop in stops]
    if not any(weights):
        weights = [1.0] * len(stops)
    total = sum(weights)
    allocated = 0
    rows = []
    for index, (stop, weight) in enumerate(zip(stops, weights)):
        qty = quantity - allocated if index == len(stops) - 1 else floor(quantity * weight / total)
        allocated += qty
        rows.append({
            "stop_id": stop.get("stop_id") or stop.get("tract_fips"),
            "qty": qty,
            "household_share": round(weight / total, 4),
        })
    return rows


def build_load_recommendation(
    route: dict[str, Any],
    inventory: list[dict[str, Any]],
    capacity_lbs: float,
) -> dict[str, Any]:
    if capacity_lbs <= 0:
        raise ValueError("capacity_lbs must be positive")
    stops = list(route.get("selected_stops") or [])
    ordered = sorted(
        inventory,
        key=lambda item: (RISK_ORDER.get(_risk(item), 99), str(item.get("item") or item.get("sku") or "")),
    )
    remaining = float(capacity_lbs)
    suggestions = []
    for item in ordered:
        available = _available_quantity(item)
        unit_lbs = _unit_weight(item)
        qty = min(floor(remaining / unit_lbs), floor(available))
        if qty <= 0:
            continue
        used_lbs = round(qty * unit_lbs, 2)
        risk = _risk(item)
        suggestions.append({
            "item_id": item.get("item_id") or item.get("sku") or item.get("item"),
            "item": item.get("item") or item.get("name") or item.get("sku"),
            "qty": qty,
            "weight_lbs": used_lbs,
            "risk_status": risk,
            "reason": f"Prioritized because cold-chain risk is {risk}; quantity is limited by on-hand inventory and remaining vehicle capacity.",
            "allocations": _quantity_allocations(stops, qty),
        })
        remaining = max(0.0, remaining - used_lbs)
        if remaining < 0.01:
            break
    return {
        "items": suggestions,
        "recommended_weight_lbs": round(capacity_lbs - remaining, 2),
        "capacity_lbs": round(capacity_lbs, 2),
        "capacity_remaining_lbs": round(remaining, 2),
        "allocation_basis": "cold-chain risk, on-hand quantity, stop household share, vehicle capacity",
        "source": "S3 inventory/on-hand.json",
    }


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
