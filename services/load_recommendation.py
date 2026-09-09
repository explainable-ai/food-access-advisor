"""Explainable, human-reviewable load suggestions from S3 inventory."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from math import floor, gcd
import os
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
ALLOCATION_MAX_STATES = max(int(os.getenv("LOAD_ALLOCATION_MAX_STATES", "6000")), 1)
ALLOCATION_MAX_CANDIDATES = max(int(os.getenv("LOAD_ALLOCATION_MAX_CANDIDATES", "250000")), 1)


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


def _matches_categories(
    item: dict[str, Any],
    requested_categories: set[str],
    excluded_categories: set[str],
) -> bool:
    tags = _categories(item)
    if tags & excluded_categories:
        return False
    return not requested_categories or bool(tags & requested_categories)


def _weight_cents(value: float) -> int:
    return max(
        int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        1,
    )


def _allocation_score(counts: tuple[int, ...], items: list[dict[str, Any]]) -> tuple[int, int, int]:
    distinct = sum(quantity > 0 for quantity in counts)
    risk_cost = sum(
        RISK_ORDER.get(_risk(item), 99) * quantity
        for item, quantity in zip(items, counts)
    )
    return distinct, -risk_cost, -sum(counts)


def _allocate_quantities(items: list[dict[str, Any]], capacity_lbs: float) -> tuple[list[int], float]:
    """Find the fullest bounded SKU combination, preferring a diverse mix."""
    if not items:
        return [], float(capacity_lbs)
    target_cents = _weight_cents(capacity_lbs)
    item_cents = [_weight_cents(_unit_weight(item)) for item in items]
    divisor = target_cents
    for weight in item_cents:
        divisor = gcd(divisor, weight)
    target = target_cents // divisor
    weights = [weight // divisor for weight in item_cents]
    empty = (0,) * len(items)
    combinations: dict[int, tuple[int, ...]] = {0: empty}
    candidates_examined = 0
    bounded = False

    for index, (item, weight) in enumerate(zip(items, weights)):
        available = min(floor(_available_quantity(item)), target // weight)
        previous = list(combinations.items())
        for current_weight, counts in previous:
            max_quantity = min(available, (target - current_weight) // weight)
            for quantity in range(1, max_quantity + 1):
                candidates_examined += 1
                if candidates_examined > ALLOCATION_MAX_CANDIDATES or len(combinations) >= ALLOCATION_MAX_STATES:
                    bounded = True
                    break
                next_weight = current_weight + quantity * weight
                candidate = list(counts)
                candidate[index] = quantity
                candidate_tuple = tuple(candidate)
                existing = combinations.get(next_weight)
                if existing is None or _allocation_score(candidate_tuple, items) > _allocation_score(existing, items):
                    combinations[next_weight] = candidate_tuple
            if bounded:
                break
        if bounded:
            break

    filled = max(combinations)
    remaining = max(0.0, float(capacity_lbs) - (filled * divisor / 100))
    return list(combinations[filled]), remaining


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
    excluded_categories: list[str] | None = None,
) -> dict[str, Any]:
    if capacity_lbs <= 0:
        raise ValueError("capacity_lbs must be positive")
    stops = list(route.get("selected_stops") or [])
    requested = {str(value).strip().lower() for value in requested_categories or [] if str(value).strip()}
    excluded = {str(value).strip().lower() for value in excluded_categories or [] if str(value).strip()}
    eligible = [item for item in inventory if _matches_categories(item, requested, excluded)]
    ordered = sorted(eligible, key=lambda item: (RISK_ORDER.get(_risk(item), 99), str(item.get("item") or item.get("sku") or "")))
    quantities, remaining = _allocate_quantities(ordered, capacity_lbs)

    suggestions = []
    covered_requested_categories: set[str] = set()
    for item, qty in zip(ordered, quantities):
        if qty <= 0:
            continue
        unit_lbs = _unit_weight(item)
        used_lbs = round(qty * unit_lbs, 2)
        risk = _risk(item)
        item_categories = _categories(item)
        matched_categories = sorted(item_categories & requested)
        covered_requested_categories.update(matched_categories)
        category_text = ", ".join(sorted(item_categories)) or "catalog item"
        suggestions.append({
            "item_id": item.get("item_id") or item.get("sku") or item.get("item"),
            "item": item.get("item") or item.get("name") or item.get("sku"),
            "qty": qty,
            "weight_lbs": used_lbs,
            "risk_status": risk,
            "category": str(item.get("category") or "") or None,
            "matched_categories": matched_categories,
            "reason": (
                f"Matches requested category ({category_text}); allocation is limited by on-hand inventory and the requested load."
                if requested
                else (
                    f"Respects requested exclusions; allocation is limited by on-hand inventory and the requested load."
                    if excluded
                    else f"Allocated from current inventory; cold-chain risk is {risk} and quantity is limited by the requested load."
                )
            ),
            "allocations": _stop_allocations(stops, qty),
        })
    return {
        "items": suggestions,
        "recommended_weight_lbs": round(capacity_lbs - remaining, 2),
        "capacity_lbs": round(capacity_lbs, 2),
        "capacity_remaining_lbs": round(remaining, 2),
        "requested_categories": sorted(requested),
        "excluded_categories": sorted(excluded),
        "category_match": requested.issubset(covered_requested_categories),
        "allocation_basis": "requested category, on-hand quantity, stop household share, requested load, cold-chain risk",
        "source": "S3 inventory/on-hand.json",
    }


@tool
def recommend_load(route: dict, inventory: list, capacity_lbs: float) -> dict:
    """Suggest a load from the current S3 inventory for human review."""
    del inventory
    current = S3InventoryStore().read(ON_HAND_KEY)
    return build_load_recommendation(route, current, capacity_lbs)
