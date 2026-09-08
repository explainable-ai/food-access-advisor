"""Explainable, human-reviewable load suggestions from S3 inventory."""

from __future__ import annotations

from math import floor
from typing import Any

from strands import tool

from storage.inventory import ON_HAND_KEY, S3InventoryStore


RISK_ORDER = {"critical": 0, "high": 1, "watch": 2, "medium": 3, "low": 4, "none": 5}


def _available_quantity(item: dict[str, Any]) -> float:
    for key in ("on_hand", "quantity", "qty"):
        if item.get(key) is not None:
            return max(float(item[key]), 0.0)
    return 0.0


def _unit_weight(item: dict[str, Any]) -> float:
    return max(float(item.get("unit_weight_lbs", item.get("weight_lbs", 1))), 0.01)


def _risk(item: dict[str, Any]) -> str:
    return str(item.get("cold_chain_risk") or item.get("risk_status") or "none").strip().lower()


def _stop_allocations(stops: list[dict[str, Any]], quantity: int) -> list[dict[str, Any]]:
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
        rows.append({"stop_id": stop.get("stop_id") or stop.get("tract_fips"), "qty": qty, "household_share": round(weight / total, 4)})
    return rows


def build_load_recommendation(route: dict[str, Any], inventory: list[dict[str, Any]], capacity_lbs: float) -> dict[str, Any]:
    if capacity_lbs <= 0:
        raise ValueError("capacity_lbs must be positive")
    stops = list(route.get("selected_stops") or [])
    ordered = sorted(inventory, key=lambda item: (RISK_ORDER.get(_risk(item), 99), str(item.get("item") or item.get("sku") or "")))
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
            "allocations": _stop_allocations(stops, qty),
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
def recommend_load(route: dict, inventory: list, capacity_lbs: float) -> dict:
    """Suggest a load from the current S3 inventory for human review."""
    del inventory
    current = S3InventoryStore().read(ON_HAND_KEY)
    return build_load_recommendation(route, current, capacity_lbs)
