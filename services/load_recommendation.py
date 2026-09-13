"""Explainable, human-reviewable load suggestions from S3 inventory."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from math import floor, gcd
import os
from typing import Any

from strands import tool

from services.equity_allocator import optimize_equitable_allocations
from storage.inventory import ON_HAND_KEY, S3InventoryStore


RISK_ORDER = {"critical": 0, "high": 1, "watch": 2, "medium": 3, "low": 4, "none": 5}
SPOILAGE_ORDER = {"rescue": 0, "use_soon": 1, "normal": 2, "unknown": 3}
SPOILAGE_BENEFIT = {"rescue": 3, "use_soon": 2, "normal": 1, "unknown": 0}
NAME_CATEGORY_HINTS = {
    "produce": ("produce", "apple", "potato", "fruit", "vegetable", "greens"),
    "dairy": ("dairy", "milk", "cheese", "egg"),
    "protein": ("protein", "chicken", "bean", "lentil"),
    "frozen": ("frozen",),
    "pantry": ("pantry", "shelf-stable", "shelf stable", "rice", "oatmeal", "grain"),
}
ALLOCATION_MAX_STATES = max(int(os.getenv("LOAD_ALLOCATION_MAX_STATES", "6000")), 1)
ALLOCATION_MAX_CANDIDATES = max(int(os.getenv("LOAD_ALLOCATION_MAX_CANDIDATES", "250000")), 1)
HOUSEHOLD_REACH_MIN_RATE = max(float(os.getenv("HOUSEHOLD_REACH_MIN_RATE", "0.01")), 0)
HOUSEHOLD_REACH_MAX_RATE = max(
    float(os.getenv("HOUSEHOLD_REACH_MAX_RATE", "0.03")), HOUSEHOLD_REACH_MIN_RATE
)
LBS_PER_HOUSEHOLD_MIN = max(float(os.getenv("LBS_PER_HOUSEHOLD_MIN", "10")), 0.01)
LBS_PER_HOUSEHOLD_MAX = max(
    float(os.getenv("LBS_PER_HOUSEHOLD_MAX", "15")), LBS_PER_HOUSEHOLD_MIN
)
SPOILAGE_RESCUE_DAYS = max(int(os.getenv("SPOILAGE_RESCUE_DAYS", "2")), 0)
SPOILAGE_USE_SOON_DAYS = max(
    int(os.getenv("SPOILAGE_USE_SOON_DAYS", "4")), SPOILAGE_RESCUE_DAYS
)


def household_load_plan(stops: list[dict[str, Any]], capacity_lbs: float) -> dict[str, Any]:
    """Convert represented households into an explainable service and load range."""
    represented = round(
        sum(max(float(stop.get("households") or 0), 0) for stop in stops)
    )
    if represented <= 0:
        return {
            "represented_households": 0,
            "service_households_min": None,
            "service_households_max": None,
            "load_range_lbs": {"min": None, "max": None},
            "target_load_lbs": round(capacity_lbs, 2),
            "method": "requested load used because household evidence was unavailable",
        }
    service_min = max(1, round(represented * HOUSEHOLD_REACH_MIN_RATE))
    service_max = max(service_min, round(represented * HOUSEHOLD_REACH_MAX_RATE))
    minimum_lbs = round(service_min * LBS_PER_HOUSEHOLD_MIN, 2)
    maximum_lbs = round(service_max * LBS_PER_HOUSEHOLD_MAX, 2)
    midpoint = (minimum_lbs + maximum_lbs) / 2
    target = round(min(float(capacity_lbs), midpoint), 2)
    return {
        "represented_households": represented,
        "service_households_min": service_min,
        "service_households_max": service_max,
        "load_range_lbs": {"min": minimum_lbs, "max": maximum_lbs},
        "target_load_lbs": target,
        "capacity_lbs": round(capacity_lbs, 2),
        "method": (
            f"{HOUSEHOLD_REACH_MIN_RATE:.0%}-{HOUSEHOLD_REACH_MAX_RATE:.0%} expected reach; "
            f"{LBS_PER_HOUSEHOLD_MIN:g}-{LBS_PER_HOUSEHOLD_MAX:g} lbs per household; "
            "capped by effective route/load capacity"
        ),
    }


def _raw_quantity(item: dict[str, Any]) -> float:
    for key in ("on_hand", "quantity", "qty"):
        if item.get(key) is not None:
            try:
                return max(float(item[key]), 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _available_quantity(item: dict[str, Any]) -> float:
    """Return allocatable units after preserving an optional warehouse reserve."""
    available = _raw_quantity(item)
    try:
        reserve = max(float(item.get("minimum_reserve") or 0), 0.0)
    except (TypeError, ValueError):
        reserve = 0.0
    return max(available - reserve, 0.0)


def _unit_weight(item: dict[str, Any]) -> float:
    try:
        value = float(item.get("unit_weight_lbs", item.get("weight_lbs", 1)))
    except (TypeError, ValueError):
        value = 1.0
    return max(value, 0.01)


def _risk(item: dict[str, Any]) -> str:
    return str(
        item.get("cold_chain_risk") or item.get("risk_status") or "none"
    ).strip().lower()


def _parse_expiration_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _days_to_spoil(item: dict[str, Any], *, today: date | None = None) -> int | None:
    explicit = item.get("days_to_spoil")
    if explicit is not None:
        try:
            return floor(float(explicit))
        except (TypeError, ValueError):
            return None
    expires = _parse_expiration_date(
        item.get("expiration_date")
        or item.get("expires_at")
        or item.get("sell_by_date")
    )
    if expires is None:
        return None
    return (expires - (today or datetime.now(timezone.utc).date())).days


def _spoilage_status(item: dict[str, Any]) -> str:
    days = _days_to_spoil(item)
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    if days <= SPOILAGE_RESCUE_DAYS:
        return "rescue"
    if days <= SPOILAGE_USE_SOON_DAYS:
        return "use_soon"
    return "normal"


def _nutrition_category(item: dict[str, Any]) -> str | None:
    value = str(item.get("nutritional_category") or "").strip().lower()
    if value:
        return value
    category = str(item.get("category") or "").strip().lower()
    return category or None


def _categories(item: dict[str, Any]) -> set[str]:
    catalog_category = str(item.get("category") or "").strip().lower()
    nutrition_category = str(item.get("nutritional_category") or "").strip().lower()
    values = {value for value in (catalog_category, nutrition_category) if value}
    values.update(catalog_category.replace("-", " ").split())
    values.update(nutrition_category.replace("-", " ").split())
    temperature_zone = str(item.get("temperature_zone") or "").strip().lower()
    if temperature_zone == "frozen":
        values.add("frozen")
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


def _eligible_inventory(
    inventory: list[dict[str, Any]],
    requested_categories: set[str],
    excluded_categories: set[str],
) -> list[dict[str, Any]]:
    return [
        item
        for item in inventory
        if _available_quantity(item) > 0
        and _spoilage_status(item) != "expired"
        and _matches_categories(item, requested_categories, excluded_categories)
    ]


def assess_inventory_feasibility(
    inventory: list[dict[str, Any]],
    requested_lbs: float,
    requested_categories: list[str] | None = None,
    excluded_categories: list[str] | None = None,
) -> dict[str, Any]:
    """Return route-planning capacity actually supportable by current inventory."""
    if requested_lbs <= 0:
        raise ValueError("requested_lbs must be positive")
    requested = {
        str(value).strip().lower()
        for value in requested_categories or []
        if str(value).strip()
    }
    excluded = {
        str(value).strip().lower()
        for value in excluded_categories or []
        if str(value).strip()
    }
    eligible = _eligible_inventory(inventory, requested, excluded)
    available_weight = round(
        sum(floor(_available_quantity(item)) * _unit_weight(item) for item in eligible),
        2,
    )
    rescue_weight = round(
        sum(
            floor(_available_quantity(item)) * _unit_weight(item)
            for item in eligible
            if _spoilage_status(item) == "rescue"
        ),
        2,
    )
    use_soon_weight = round(
        sum(
            floor(_available_quantity(item)) * _unit_weight(item)
            for item in eligible
            if _spoilage_status(item) == "use_soon"
        ),
        2,
    )
    covered: set[str] = set()
    for item in eligible:
        covered.update(_categories(item) & requested)
    missing_categories = sorted(requested - covered)
    effective = round(min(float(requested_lbs), available_weight), 2)
    if effective <= 0:
        status = "blocked"
    elif available_weight < requested_lbs or missing_categories:
        status = "partial"
    else:
        status = "ready"
    return {
        "status": status,
        "requested_load_lbs": round(float(requested_lbs), 2),
        "available_weight_lbs": available_weight,
        "effective_load_lbs": effective,
        "eligible_item_count": len(eligible),
        "requested_categories": sorted(requested),
        "excluded_categories": sorted(excluded),
        "missing_categories": missing_categories,
        "rescue_candidate_weight_lbs": rescue_weight,
        "use_soon_weight_lbs": use_soon_weight,
        "source": "S3 inventory/on-hand.json",
        "method": (
            "allocatable on-hand units × unit weight, excluding expired inventory, "
            "warehouse minimum reserves, and explicitly excluded categories"
        ),
    }


def _weight_cents(value: float) -> int:
    return max(
        int(
            (Decimal(str(value)) * 100).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        ),
        1,
    )


def _allocation_score(
    counts: tuple[int, ...], items: list[dict[str, Any]]
) -> tuple[int, int, int, int]:
    spoilage_benefit = sum(
        SPOILAGE_BENEFIT.get(_spoilage_status(item), 0) * quantity
        for item, quantity in zip(items, counts)
    )
    distinct = sum(quantity > 0 for quantity in counts)
    risk_cost = sum(
        RISK_ORDER.get(_risk(item), 99) * quantity
        for item, quantity in zip(items, counts)
    )
    return spoilage_benefit, distinct, -risk_cost, -sum(counts)


def _allocate_quantities(
    items: list[dict[str, Any]], capacity_lbs: float
) -> tuple[list[int], float]:
    """Fill payload deterministically, favoring at-risk food on equal weight."""
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
                if (
                    candidates_examined > ALLOCATION_MAX_CANDIDATES
                    or len(combinations) >= ALLOCATION_MAX_STATES
                ):
                    bounded = True
                    break
                next_weight = current_weight + quantity * weight
                candidate = list(counts)
                candidate[index] = quantity
                candidate_tuple = tuple(candidate)
                existing = combinations.get(next_weight)
                if existing is None or _allocation_score(
                    candidate_tuple, items
                ) > _allocation_score(existing, items):
                    combinations[next_weight] = candidate_tuple
            if bounded:
                break
        if bounded:
            break
    filled = max(combinations)
    remaining = max(0.0, float(capacity_lbs) - (filled * divisor / 100))
    return list(combinations[filled]), remaining


def _nutrition_mix(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, float] = {}
    total_weight = sum(float(item.get("weight_lbs") or 0) for item in suggestions)
    for item in suggestions:
        category = str(
            item.get("nutritional_category")
            or item.get("category")
            or "unclassified"
        )
        totals[category] = totals.get(category, 0.0) + float(
            item.get("weight_lbs") or 0
        )
    return [
        {
            "nutritional_category": category,
            "weight_lbs": round(weight, 2),
            "share": round(weight / total_weight, 4) if total_weight else 0.0,
        }
        for category, weight in sorted(totals.items())
    ]


def _stop_reserves_from_objective(
    allocation_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for stop in allocation_plan.get("stop_status") or []:
        rows.append(
            {
                "stop_id": stop.get("stop_id"),
                "sequence": stop.get("sequence"),
                "name": stop.get("name"),
                "need_score": stop.get("need_score"),
                "reserved_weight_lbs": stop.get("allocated_weight_lbs", 0.0),
                "demand_capacity_lbs": stop.get("capacity_lbs", 0.0),
                "reserve_protected": bool(stop.get("reserve_protected")),
            }
        )
    return rows


def build_load_recommendation(
    route: dict[str, Any],
    inventory: list[dict[str, Any]],
    capacity_lbs: float,
    requested_categories: list[str] | None = None,
    excluded_categories: list[str] | None = None,
) -> dict[str, Any]:
    """Build Dispatch's deterministic load and joint item-to-stop allocation."""
    if capacity_lbs <= 0:
        raise ValueError("capacity_lbs must be positive")
    stops = list(route.get("selected_stops") or [])
    requested = {
        str(value).strip().lower()
        for value in requested_categories or []
        if str(value).strip()
    }
    excluded = {
        str(value).strip().lower()
        for value in excluded_categories or []
        if str(value).strip()
    }
    household_plan = household_load_plan(stops, capacity_lbs)
    target_lbs = float(household_plan["target_load_lbs"])
    feasibility = assess_inventory_feasibility(
        inventory,
        target_lbs,
        requested_categories=sorted(requested),
        excluded_categories=sorted(excluded),
    )
    eligible = _eligible_inventory(inventory, requested, excluded)
    ordered = sorted(
        eligible,
        key=lambda item: (
            SPOILAGE_ORDER.get(_spoilage_status(item), 99),
            RISK_ORDER.get(_risk(item), 99),
            str(item.get("item") or item.get("sku") or ""),
        ),
    )
    quantities, _ = _allocate_quantities(ordered, target_lbs)

    provisional: list[dict[str, Any]] = []
    for item, qty in zip(ordered, quantities):
        if qty <= 0:
            continue
        risk = _risk(item)
        spoilage_status = _spoilage_status(item)
        days_to_spoil = _days_to_spoil(item)
        item_categories = _categories(item)
        matched_categories = sorted(item_categories & requested)
        category_text = ", ".join(sorted(item_categories)) or "catalog item"
        reason_parts = []
        if spoilage_status == "rescue":
            reason_parts.append(
                f"rescue candidate ({days_to_spoil} day(s) to spoilage)"
            )
        elif spoilage_status == "use_soon":
            reason_parts.append(
                f"use-soon inventory ({days_to_spoil} day(s) to spoilage)"
            )
        if requested:
            reason_parts.append(f"matches requested category ({category_text})")
        elif excluded:
            reason_parts.append("respects requested category exclusions")
        else:
            reason_parts.append(f"cold-chain risk is {risk}")
        reason_parts.append(
            "selected quantity is bounded by allocatable on-hand inventory and payload"
        )
        provisional.append(
            {
                "item_id": item.get("item_id") or item.get("sku") or item.get("item"),
                "item": item.get("item") or item.get("name") or item.get("sku"),
                "qty": qty,
                "selected_qty": qty,
                "weight_lbs": round(qty * _unit_weight(item), 2),
                "unit_weight_lbs": _unit_weight(item),
                "risk_status": risk,
                "spoilage_status": spoilage_status,
                "days_to_spoil": days_to_spoil,
                "distribution_mode": (
                    "rescue_review" if spoilage_status == "rescue" else "standard"
                ),
                "category": str(item.get("category") or "") or None,
                "nutritional_category": _nutrition_category(item),
                "temperature_zone": item.get("temperature_zone") or item.get("storage_zone"),
                "matched_categories": matched_categories,
                "cultural_tags": item.get("cultural_tags"),
                "dietary_tags": item.get("dietary_tags"),
                "max_allocation_per_household": item.get(
                    "max_allocation_per_household"
                ),
                "reason": "; ".join(reason_parts) + ".",
                "allocations": [],
            }
        )

    allocation_plan = optimize_equitable_allocations(
        stops,
        provisional,
        requested_categories=sorted(requested),
    )
    allocations_by_item = allocation_plan.get("allocations_by_item") or {}
    suggestions: list[dict[str, Any]] = []
    covered_requested_categories: set[str] = set()
    loaded_by_id: dict[str, float] = {}
    for item in provisional:
        item_id = str(item.get("item_id") or item.get("item"))
        allocations = list(allocations_by_item.get(item_id) or [])
        allocated_qty = sum(int(row.get("qty") or 0) for row in allocations)
        if allocated_qty <= 0:
            continue
        selected_qty = int(item.get("selected_qty") or item.get("qty") or 0)
        loaded_weight = round(allocated_qty * float(item["unit_weight_lbs"]), 2)
        loaded_by_id[item_id] = loaded_weight
        loaded = {
            **item,
            "qty": allocated_qty,
            "weight_lbs": loaded_weight,
            "allocations": allocations,
        }
        if allocated_qty < selected_qty:
            loaded["unallocated_selected_qty"] = selected_qty - allocated_qty
            loaded["reason"] = (
                str(loaded.get("reason") or "")
                + " Some selected units were left off because stop demand/cap constraints could not accept them."
            )
        suggestions.append(loaded)
        covered_requested_categories.update(
            set(loaded.get("matched_categories") or [])
        )

    recommended_weight = round(
        sum(float(item.get("weight_lbs") or 0) for item in suggestions), 2
    )
    stop_reserves = _stop_reserves_from_objective(allocation_plan)
    all_stops_protected = bool(stop_reserves) and all(
        bool(row.get("reserve_protected")) for row in stop_reserves
    )

    rescue_recommendations = []
    for item in ordered:
        if _spoilage_status(item) != "rescue":
            continue
        item_id = str(item.get("item_id") or item.get("sku") or item.get("item"))
        available_lbs = round(
            floor(_available_quantity(item)) * _unit_weight(item), 2
        )
        loaded_lbs = round(loaded_by_id.get(item_id, 0.0), 2)
        remaining_lbs = round(max(available_lbs - loaded_lbs, 0.0), 2)
        if available_lbs <= 0:
            continue
        rescue_recommendations.append(
            {
                "item_id": item_id,
                "item": item.get("item") or item.get("name") or item.get("sku"),
                "days_to_spoil": _days_to_spoil(item),
                "available_weight_lbs": available_lbs,
                "planned_route_weight_lbs": loaded_lbs,
                "unallocated_at_risk_weight_lbs": remaining_lbs,
                "recommended_action": (
                    "Prioritize planned-stop allocation; if at-risk inventory remains, "
                    "propose an approved partner transfer or free rescue distribution."
                ),
                "human_approval_required": True,
            }
        )

    return {
        "items": suggestions,
        "recommended_weight_lbs": recommended_weight,
        "target_load_lbs": round(target_lbs, 2),
        "capacity_lbs": round(capacity_lbs, 2),
        "capacity_remaining_lbs": round(capacity_lbs - recommended_weight, 2),
        "household_plan": household_plan,
        "inventory_feasibility": feasibility,
        "requested_categories": sorted(requested),
        "excluded_categories": sorted(excluded),
        "category_match": requested.issubset(covered_requested_categories),
        "nutrition_mix": _nutrition_mix(suggestions),
        "stop_reserves": stop_reserves,
        "all_stops_protected": all_stops_protected,
        "rescue_recommendations": rescue_recommendations,
        "spoilage_summary": {
            "rescue_candidate_weight_lbs": feasibility[
                "rescue_candidate_weight_lbs"
            ],
            "use_soon_weight_lbs": feasibility["use_soon_weight_lbs"],
            "rescue_recommendation_count": len(rescue_recommendations),
        },
        "dispatch_objective": allocation_plan.get("objective") or {},
        "unallocated_selected_inventory": allocation_plan.get(
            "unallocated_items"
        )
        or [],
        "equity_policy": (
            "Protect a minimum reserve for planned stops when feasible, then maximize "
            "a deterministic weighted objective over Scout vulnerability, explicit nutrition "
            "match, and inverse days-to-spoil priority."
        ),
        "allocation_basis": (
            "deterministic payload knapsack plus joint item-to-stop Knapsack of Equity; "
            "inventory, stop demand, reserve protection, optional per-household caps, "
            "Scout need score, nutrition match, spoilage pressure, and cold-chain evidence"
        ),
        "human_approval_required": True,
        "source": "S3 inventory/on-hand.json",
    }


@tool
def recommend_load(route: dict, inventory: list, capacity_lbs: float) -> dict:
    """Suggest an equitable, spoilage-aware load from current S3 inventory for human review."""
    del inventory
    current = S3InventoryStore().read(ON_HAND_KEY)
    return build_load_recommendation(route, current, capacity_lbs)
