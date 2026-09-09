"""Explainable, human-reviewable load suggestions from S3 inventory."""

from __future__ import annotations

from math import floor
from typing import Any

from strands import tool

from storage.inventory import ON_HAND_KEY, S3InventoryStore


RISK_ORDER = {"critical": 0, "high": 1, "watch": 2, "medium": 3, "low": 4, "none": 5}
NAME_CATEGORY_HINTS = {
    "produce": ("produce", "apple", "potato", "fruit", "vegetable"),
    "dairy": ("dairy", "milk", "cheese", "egg"),
    "protein": ("protein", "chicken", "bean"),
    "frozen": ("frozen",),
    "pantry": ("pantry", "shelf-stable", "shelf stable", "rice", "oatmeal", "grain"),
}


def _available_quantity(item: dict[str, Any]) -> float:
    for key in ("on_hand", "quantity", "qty"):
        if item.get(key) is not None:
            return max(float(item[key]), 0.0)
    return 0.0


def _unit_weight(item: dict[str, Any]) -> float:
    return max(float(item.get("unit_weight_lbs", item.get("weight_lbs", 1))), 0.01)


def _risk(item: dict[str, Any]) -> str:
    return str(item.get("cold_chain_risk") or item.get("risk_status") or "none").strip().lower()


def _categories(item: dict[str, Any]) -> set[str]:
    catalog_category = str(item.get("category") or "").strip().lower()
    values = {catalog_category} if catalog_category else set()
    values.update(catalog_category.replace("-", " ").split())
    temperature_zone = str(item.get("temperature_zone") or "").strip().lower()
    if temperature_zone == "frozen":
        values.add("frozen")

    # Compatibility for the already-deployed inventory object, which predates
    # the catalog category field. New objects should carry `category` directly.
    item_name = str(item.get("item") or item.get("name") or "").strip().lower()
    for category, hints in NAME_CATEGORY_HINTS.items():
        if any(hint in item_name for hint in hints):
            values.add(category)
    return values


def _matches_categories(item: dict[str, Any], requested_categories: set[str]) -> bool:
    if not requested_categories:
        return True
    tags = _categories(item)
    return bool(tags & requested_categories)


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


def build_load_recommendation(
    route: dict[str, Any],
    inventory: list[dict[str, Any]],
    capacity_lbs: float,
    requested_categories: list[str] | None = None,
) -> dict[str, Any]:
    if capacity_lbs <= 0:
        raise ValueError("capacity_lbs must be positive")
    stops = list(route.get("selected_stops") or [])
    requested = {str(value).strip().lower() for value in requested_categories or [] if str(value).strip()}
    eligible = [item for item in inventory if _matches_categories(item, requested)]
    ordered = sorted(eligible, key=lambda item: (RISK_ORDER.get(_risk(item), 99), str(item.get("item") or item.get("sku") or "")))
    remaining = float(capacity_lbs)
    quantities = [0] * len(ordered)

    # Allocate in explainable rounds so a broad request such as "produce"
    # yields a useful mix instead of being monopolized by the first SKU.
    while remaining >= 0.01:
        progress = False
        for index, item in enumerate(ordered):
            unit_lbs = _unit_weight(item)
            if quantities[index] >= floor(_available_quantity(item)):
                continue
            if unit_lbs > remaining + 1e-9:
                continue
            quantities[index] += 1
            remaining = max(0.0, remaining - unit_lbs)
            progress = True
        if not progress:
            break

    suggestions = []
    for item, qty in zip(ordered, quantities):
        if qty <= 0:
            continue
        unit_lbs = _unit_weight(item)
        used_lbs = round(qty * unit_lbs, 2)
        risk = _risk(item)
        category_text = ", ".join(sorted(_categories(item))) or "catalog item"
        suggestions.append({
            "item_id": item.get("item_id") or item.get("sku") or item.get("item"),
            "item": item.get("item") or item.get("name") or item.get("sku"),
            "qty": qty,
            "weight_lbs": used_lbs,
            "risk_status": risk,
            "category": str(item.get("category") or "") or None,
            "matched_categories": sorted(_categories(item) & requested),
            "reason": (
                f"Matches requested category ({category_text}); allocation is limited by on-hand inventory and the requested load."
                if requested
                else f"Allocated from current inventory; cold-chain risk is {risk} and quantity is limited by the requested load."
            ),
            "allocations": _stop_allocations(stops, qty),
        })
    return {
        "items": suggestions,
        "recommended_weight_lbs": round(capacity_lbs - remaining, 2),
        "capacity_lbs": round(capacity_lbs, 2),
        "capacity_remaining_lbs": round(remaining, 2),
        "requested_categories": sorted(requested),
        "category_match": not requested or bool(eligible),
        "allocation_basis": "requested category, on-hand quantity, stop household share, requested load, cold-chain risk",
        "source": "S3 inventory/on-hand.json",
    }


@tool
def recommend_load(route: dict, inventory: list, capacity_lbs: float) -> dict:
    """Suggest a load from the current S3 inventory for human review."""
    del inventory
    current = S3InventoryStore().read(ON_HAND_KEY)
    return build_load_recommendation(route, current, capacity_lbs)
