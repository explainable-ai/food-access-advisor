"""Deterministic joint item-to-stop allocation for LastMile Market.

This module implements Dispatch's "Knapsack of Equity" objective without an
LLM deciding quantities and without introducing a heavyweight solver runtime.
The selected load is allocated across route stops using an explicit linear
utility function and hard operational constraints.

Objective for item i at stop s::

    utility[i,s] = w_v * vulnerability[s]
                 + w_n * nutrition_match[i,s]
                 + w_s * spoilage_priority[i]

    maximize sum(Q[i,s] * utility[i,s])

The allocator protects a minimum reserve at planned stops first, then assigns
remaining units by objective value per pound while respecting stop demand,
on-hand selected quantities, and optional per-household item caps. The method
is deterministic and bounded, but is intentionally not represented as an
exact MILP solution.
"""

from __future__ import annotations

from math import floor, isfinite
import os
from typing import Any


DEFAULT_OBJECTIVE_WEIGHTS = {
    "vulnerability": 0.50,
    "nutrition": 0.30,
    "spoilage": 0.20,
}
CATEGORY_ALIASES = {
    "fresh produce": {"fresh produce", "produce", "fruit", "vegetable", "greens"},
    "produce": {"fresh produce", "produce", "fruit", "vegetable", "greens"},
    "protein": {"protein", "legume", "legumes", "bean", "beans", "lentil", "lentils"},
    "whole grain": {"whole grain", "whole grains", "grain", "grains", "pantry"},
    "whole grains": {"whole grain", "whole grains", "grain", "grains", "pantry"},
    "dairy": {"dairy", "milk", "cheese", "egg", "eggs"},
}


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(value, minimum)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(value, minimum)


def objective_weights() -> dict[str, float]:
    """Return normalized policy weights; values are configuration, not model output."""
    raw = {
        "vulnerability": _env_float(
            "DISPATCH_OBJECTIVE_VULNERABILITY_WEIGHT",
            DEFAULT_OBJECTIVE_WEIGHTS["vulnerability"],
        ),
        "nutrition": _env_float(
            "DISPATCH_OBJECTIVE_NUTRITION_WEIGHT",
            DEFAULT_OBJECTIVE_WEIGHTS["nutrition"],
        ),
        "spoilage": _env_float(
            "DISPATCH_OBJECTIVE_SPOILAGE_WEIGHT",
            DEFAULT_OBJECTIVE_WEIGHTS["spoilage"],
        ),
    }
    total = sum(raw.values())
    if total <= 0:
        return dict(DEFAULT_OBJECTIVE_WEIGHTS)
    return {key: value / total for key, value in raw.items()}


def _text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_tag(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())


def _expanded_tags(values: list[str]) -> set[str]:
    tags: set[str] = set()
    for value in values:
        normalized = _normalize_tag(value)
        if not normalized:
            continue
        tags.add(normalized)
        tags.update(CATEGORY_ALIASES.get(normalized, set()))
    return tags


def item_nutrition_tags(item: dict[str, Any]) -> set[str]:
    values: list[str] = []
    for key in (
        "nutritional_category",
        "category",
        "matched_categories",
        "cultural_tags",
        "dietary_tags",
    ):
        values.extend(_text_values(item.get(key)))
    return _expanded_tags(values)


def stop_nutrition_priorities(stop: dict[str, Any]) -> set[str]:
    """Use only explicit stop/community demand fields; never infer from demographics."""
    values: list[str] = []
    for key in (
        "nutritional_priorities",
        "requested_nutritional_categories",
        "community_requested_categories",
        "requested_categories",
        "community_requested_tags",
        "preference_tags",
    ):
        values.extend(_text_values(stop.get(key)))
    return _expanded_tags(values)


def _need_score(stop: dict[str, Any]) -> float:
    raw = stop.get("neighborhood_vulnerability_score")
    if raw is None:
        raw = stop.get("need_score")
    try:
        return min(max(float(raw or 0.0), 0.0), 100.0)
    except (TypeError, ValueError):
        return 0.0


def _households(stop: dict[str, Any]) -> float:
    for key in ("households", "households_total"):
        if stop.get(key) is None:
            continue
        try:
            return max(float(stop[key]), 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


def vulnerability_score(stop: dict[str, Any]) -> float:
    return round(_need_score(stop) / 100.0, 6)


def spoilage_priority(item: dict[str, Any]) -> float:
    """Return inverse-days spoilage pressure in [0, 1]."""
    raw_days = item.get("days_to_spoil")
    if raw_days is not None:
        try:
            days = float(raw_days)
        except (TypeError, ValueError):
            days = None
        if days is not None:
            if days < 0:
                return 0.0
            return round(1.0 / (days + 1.0), 6)
    status = str(item.get("spoilage_status") or "unknown").strip().lower()
    return {
        "rescue": 1.0,
        "use_soon": 0.50,
        "normal": 0.10,
        "unknown": 0.0,
    }.get(status, 0.0)


def nutrition_match(
    item: dict[str, Any],
    stop: dict[str, Any],
    mission_categories: set[str],
) -> tuple[float, str]:
    item_tags = item_nutrition_tags(item)
    stop_tags = stop_nutrition_priorities(stop)
    if stop_tags:
        return (1.0 if item_tags & stop_tags else 0.0), "explicit_stop_or_community_request"
    if mission_categories:
        return (1.0 if item_tags & mission_categories else 0.0), "mission_requested_category"
    neutral = min(
        _env_float("DISPATCH_NUTRITION_NEUTRAL_SCORE", 0.50),
        1.0,
    )
    return neutral, "neutral_no_explicit_preference_data"


def objective_components(
    item: dict[str, Any],
    stop: dict[str, Any],
    mission_categories: set[str],
) -> dict[str, Any]:
    weights = objective_weights()
    vulnerability = vulnerability_score(stop)
    nutrition, nutrition_basis = nutrition_match(item, stop, mission_categories)
    spoilage = spoilage_priority(item)
    contributions = {
        "vulnerability": weights["vulnerability"] * vulnerability,
        "nutrition": weights["nutrition"] * nutrition,
        "spoilage": weights["spoilage"] * spoilage,
    }
    return {
        "vulnerability": round(vulnerability, 6),
        "nutrition_match": round(nutrition, 6),
        "spoilage_priority": round(spoilage, 6),
        "nutrition_match_basis": nutrition_basis,
        "weighted_contributions": {
            key: round(value, 6) for key, value in contributions.items()
        },
        "score_per_unit": round(sum(contributions.values()), 6),
    }


def _unit_weight(item: dict[str, Any]) -> float:
    try:
        value = float(item.get("unit_weight_lbs") or 1.0)
    except (TypeError, ValueError):
        value = 1.0
    return max(value, 0.01)


def _selected_quantity(item: dict[str, Any]) -> int:
    try:
        return max(floor(float(item.get("qty") or 0)), 0)
    except (TypeError, ValueError):
        return 0


def _stop_weight_capacities(
    stops: list[dict[str, Any]], items: list[dict[str, Any]]
) -> list[float]:
    selected_weight = sum(_selected_quantity(item) * _unit_weight(item) for item in items)
    explicit: list[float | None] = []
    for stop in stops:
        raw = stop.get("demand")
        try:
            value = max(float(raw), 0.0) if raw is not None else None
        except (TypeError, ValueError):
            value = None
        explicit.append(value if value and value > 0 else None)
    if all(value is not None for value in explicit):
        return [float(value) for value in explicit]

    household_weights = []
    for stop in stops:
        households = _households(stop)
        household_weights.append(households if households > 0 else 1.0)
    denominator = sum(household_weights) or float(len(stops) or 1)
    return [selected_weight * weight / denominator for weight in household_weights]


def _item_stop_unit_limit(item: dict[str, Any], stop: dict[str, Any]) -> int | None:
    raw = item.get("max_allocation_per_household")
    if raw is None:
        return None
    try:
        per_household = max(float(raw), 0.0)
    except (TypeError, ValueError):
        return None
    households = _households(stop)
    if households <= 0:
        return None
    return max(floor(per_household * households), 0)


def optimize_equitable_allocations(
    stops: list[dict[str, Any]],
    items: list[dict[str, Any]],
    requested_categories: list[str] | None = None,
) -> dict[str, Any]:
    """Allocate selected item quantities across stops using Dispatch's objective.

    Stage 1 protects a configurable minimum unit reserve at planned stops when
    feasible. Stage 2 repeatedly assigns the best feasible item/stop pair by
    objective value per pound. This is deterministic and bounded; it is not
    advertised as an exact mixed-integer optimum.
    """
    if not stops or not items:
        return {
            "allocations_by_item": {},
            "objective": _objective_summary([], [], [], set()),
            "stop_status": [],
            "unallocated_items": [],
        }

    mission_categories = _expanded_tags([str(value) for value in requested_categories or []])
    stop_caps = _stop_weight_capacities(stops, items)
    stop_used = [0.0 for _ in stops]
    remaining = [_selected_quantity(item) for item in items]
    allocated_counts = [[0 for _ in stops] for _ in items]
    components = [
        [objective_components(item, stop, mission_categories) for stop in stops]
        for item in items
    ]
    min_reserve_units = _env_int("MIN_STOP_RESERVE_UNITS", 1)

    def feasible_units(item_index: int, stop_index: int) -> int:
        if remaining[item_index] <= 0:
            return 0
        weight = _unit_weight(items[item_index])
        capacity_units = floor(max(stop_caps[stop_index] - stop_used[stop_index], 0.0) / weight + 1e-9)
        limit = _item_stop_unit_limit(items[item_index], stops[stop_index])
        if limit is not None:
            capacity_units = min(
                capacity_units,
                max(limit - allocated_counts[item_index][stop_index], 0),
            )
        return max(min(remaining[item_index], capacity_units), 0)

    # Reserve lock: when supply is scarce, protect higher-need stops first; when
    # supply is sufficient every stop receives its reserve before objective fill.
    reserve_order = sorted(
        range(len(stops)),
        key=lambda index: (_need_score(stops[index]), -int(stops[index].get("sequence") or index + 1)),
        reverse=True,
    )
    for stop_index in reserve_order:
        reserved = 0
        while reserved < min_reserve_units:
            candidates = []
            for item_index, item in enumerate(items):
                if feasible_units(item_index, stop_index) <= 0:
                    continue
                score = float(components[item_index][stop_index]["score_per_unit"])
                weight = _unit_weight(item)
                candidates.append((score / weight, score, -weight, -item_index, item_index))
            if not candidates:
                break
            item_index = max(candidates)[-1]
            allocated_counts[item_index][stop_index] += 1
            remaining[item_index] -= 1
            stop_used[stop_index] += _unit_weight(items[item_index])
            reserved += 1

    # Objective fill: linear utility per assigned unit, with payload expressed
    # through the remaining per-stop weight caps.
    while any(quantity > 0 for quantity in remaining):
        best = None
        for item_index, item in enumerate(items):
            if remaining[item_index] <= 0:
                continue
            weight = _unit_weight(item)
            for stop_index, stop in enumerate(stops):
                max_units = feasible_units(item_index, stop_index)
                if max_units <= 0:
                    continue
                score = float(components[item_index][stop_index]["score_per_unit"])
                candidate = (
                    score / weight,
                    score,
                    _need_score(stop),
                    -int(stop.get("sequence") or stop_index + 1),
                    -item_index,
                    item_index,
                    stop_index,
                    max_units,
                )
                if best is None or candidate[:5] > best[:5]:
                    best = candidate
        if best is None:
            break
        item_index, stop_index, max_units = best[5], best[6], best[7]
        allocated_counts[item_index][stop_index] += max_units
        remaining[item_index] -= max_units
        stop_used[stop_index] += max_units * _unit_weight(items[item_index])

    allocations_by_item: dict[str, list[dict[str, Any]]] = {}
    for item_index, item in enumerate(items):
        item_id = str(item.get("item_id") or item.get("sku") or item.get("item") or item_index)
        rows = []
        for stop_index, stop in enumerate(stops):
            qty = allocated_counts[item_index][stop_index]
            if qty <= 0:
                continue
            rows.append(
                {
                    "stop_id": stop.get("stop_id") or stop.get("tract_fips"),
                    "sequence": stop.get("sequence") or stop_index + 1,
                    "name": stop.get("name"),
                    "qty": qty,
                    "weight_lbs": round(qty * _unit_weight(item), 2),
                    "need_score": round(_need_score(stop), 2),
                    "reserve_protected": True,
                    "objective": components[item_index][stop_index],
                }
            )
        allocations_by_item[item_id] = rows

    stop_status = []
    for index, stop in enumerate(stops):
        stop_status.append(
            {
                "stop_id": stop.get("stop_id") or stop.get("tract_fips"),
                "sequence": stop.get("sequence") or index + 1,
                "name": stop.get("name"),
                "need_score": round(_need_score(stop), 2),
                "capacity_lbs": round(stop_caps[index], 2),
                "allocated_weight_lbs": round(stop_used[index], 2),
                "reserve_protected": any(
                    allocated_counts[item_index][index] > 0 for item_index in range(len(items))
                ),
            }
        )

    unallocated_items = []
    for item_index, quantity in enumerate(remaining):
        if quantity <= 0:
            continue
        item = items[item_index]
        unallocated_items.append(
            {
                "item_id": item.get("item_id") or item.get("sku") or item.get("item"),
                "qty": quantity,
                "weight_lbs": round(quantity * _unit_weight(item), 2),
                "reason": "stop demand/capacity or per-household allocation constraints",
            }
        )

    return {
        "allocations_by_item": allocations_by_item,
        "objective": _objective_summary(items, stops, allocated_counts, mission_categories),
        "stop_status": stop_status,
        "unallocated_items": unallocated_items,
    }


def _objective_summary(
    items: list[dict[str, Any]],
    stops: list[dict[str, Any]],
    allocated_counts: list[list[int]],
    mission_categories: set[str],
) -> dict[str, Any]:
    weights = objective_weights()
    totals = {
        "vulnerability": 0.0,
        "nutrition": 0.0,
        "spoilage": 0.0,
    }
    score = 0.0
    units = 0
    allocated_weight = 0.0
    if items and stops and allocated_counts:
        for item_index, item in enumerate(items):
            for stop_index, stop in enumerate(stops):
                qty = allocated_counts[item_index][stop_index]
                if qty <= 0:
                    continue
                component = objective_components(item, stop, mission_categories)
                contributions = component["weighted_contributions"]
                for key in totals:
                    totals[key] += qty * float(contributions[key])
                score += qty * float(component["score_per_unit"])
                units += qty
                allocated_weight += qty * _unit_weight(item)
    return {
        "name": "knapsack_of_equity_v1",
        "formula": (
            "maximize sum(Q[i,s] * (w_v*V[s] + w_n*N[i,s] + w_s*S[i]))"
        ),
        "weights": {key: round(value, 6) for key, value in weights.items()},
        "total_score": round(score, 6),
        "average_score_per_unit": round(score / units, 6) if units else 0.0,
        "allocated_units": units,
        "allocated_weight_lbs": round(allocated_weight, 2),
        "weighted_component_totals": {
            key: round(value, 6) for key, value in totals.items()
        },
        "variables": {
            "Q[i,s]": "integer quantity of selected item i assigned to stop s",
            "V[s]": "Scout need/vulnerability score normalized to 0-1",
            "N[i,s]": "explicit nutrition/community request match; neutral when no preference evidence exists",
            "S[i]": "inverse days-to-spoil priority; higher when spoilage is closer",
        },
        "constraints": [
            "selected item quantity cannot exceed the deterministic load plan",
            "per-stop allocated weight cannot exceed Router stop demand/capacity",
            "minimum stop reserve units are protected when feasible",
            "optional max_allocation_per_household is enforced when supplied",
            "expired and otherwise ineligible inventory is excluded upstream",
            "vehicle payload is enforced upstream by the deterministic load knapsack",
        ],
        "method": "deterministic bounded linear-objective allocator; greedy by utility per pound after reserve locks",
        "exact_milp": False,
        "human_review_required": True,
    }
