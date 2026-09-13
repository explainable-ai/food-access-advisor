"""Deterministic, read-only Mission Operations preview assembly."""

from __future__ import annotations

import os
from time import perf_counter
from typing import Any
from uuid import uuid4

from services.community_intelligence import enrich_route_with_community_intelligence
from services.load_recommendation import build_load_recommendation
from storage.inventory import COLD_CHAIN_KEY, ON_HAND_KEY, S3InventoryStore
from storage.operations_repository import OperationsRepository


CHECK_LABELS = {
    "inventory": "Inventory",
    "vehicle": "Vehicle",
    "driver": "Driver",
    "site": "Site partner",
    "permit": "Permit",
    "policy": "Manifest policy",
}
KNOWN_COLD_CHAIN_RISKS = {"critical", "high", "watch", "medium", "low", "none"}


def _canonical_status(scenario: dict[str, Any]) -> str:
    inventory = str(scenario.get("inventory_answer", "")).strip().lower()
    expected = str(scenario.get("expected_mission_status", "")).strip().lower()
    if inventory == "unknown" or "refresh required" in expected:
        return "Unknown"
    if inventory == "partial" or "substitution" in expected:
        return "Partial"
    if expected.startswith("ready"):
        return "Ready"
    if expected.startswith("blocked"):
        return "Blocked"
    return "Unknown"


def _record(
    repository: OperationsRepository, entity_type: str, entity_id: Any
) -> dict[str, Any] | None:
    return repository.get_entity(entity_type, str(entity_id)) if entity_id else None


def _check(
    kind: str,
    status: str,
    detail: str,
    evidence_id: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": CHECK_LABELS[kind],
        "status": status,
        "detail": detail,
        "evidence_id": evidence_id,
        "finding": detail,
        "data_used": [evidence_id] if evidence_id else [],
    }


def build_mission_preview(
    scenario: dict[str, Any], repository: OperationsRepository
) -> dict[str, Any]:
    """Return evidence-backed preview data without reserving or dispatching anything."""
    status = _canonical_status(scenario)
    explanation = str(
        scenario.get("expected_agent_explanation")
        or "The scenario does not include an operational explanation."
    )
    explanation_lower = explanation.lower()

    vehicle_id = scenario.get("vehicle_id")
    driver_id = scenario.get("driver_id")
    site_id = scenario.get("site_id")
    permit_id = scenario.get("permit_id")

    vehicle = _record(repository, "vehicle", vehicle_id)
    driver = _record(repository, "driver", driver_id)
    site = _record(repository, "site_partner", site_id)
    permit = _record(repository, "permit", permit_id)

    inventory_answer = str(scenario.get("inventory_answer", "Unknown")).strip().lower()
    inventory_status = {
        "yes": "Ready",
        "partial": "Partial",
        "unknown": "Unknown",
    }.get(inventory_answer, "Unknown")

    checks = [
        _check(
            "inventory",
            inventory_status,
            explanation
            if inventory_status != "Ready"
            else "Required inventory is reported as current and sufficient.",
            str(scenario.get("warehouse_id") or "") or None,
        ),
        _check(
            "vehicle",
            "Blocked"
            if "vehicle is unavailable" in explanation_lower
            or "overdue maintenance" in explanation_lower
            else ("Ready" if vehicle else "Unknown"),
            (
                "Assigned vehicle is unavailable because maintenance is overdue."
                if "overdue maintenance" in explanation_lower
                else ("Assigned vehicle record verified." if vehicle else "Assigned vehicle evidence is missing.")
            ),
            str(vehicle_id) if vehicle_id else None,
        ),
        _check(
            "driver",
            "Blocked"
            if "driver credentials are invalid" in explanation_lower
            else ("Ready" if driver else "Unknown"),
            (
                "Assigned driver credentials are invalid."
                if "driver credentials are invalid" in explanation_lower
                else ("Assigned driver record verified." if driver else "Assigned driver evidence is missing.")
            ),
            str(driver_id) if driver_id else None,
        ),
        _check(
            "site",
            "Ready" if site else "Unknown",
            "Site partner record verified." if site else "Site partner evidence is missing.",
            str(site_id) if site_id else None,
        ),
        _check(
            "permit",
            "Blocked"
            if "permit is missing" in explanation_lower
            else ("Ready" if permit else "Unknown"),
            (
                "Required permit is missing."
                if "permit is missing" in explanation_lower
                else ("Permit record verified." if permit else "Permit evidence is missing.")
            ),
            str(permit_id) if permit_id else None,
        ),
        _check(
            "policy",
            "Partial"
            if status == "Partial"
            else ("Unknown" if status == "Unknown" else "Ready"),
            (
                "Authorized substitution or a reduced household count is required."
                if status == "Partial"
                else (
                    "Fresh evidence is required before policy can be evaluated."
                    if status == "Unknown"
                    else "Applicable manifest policy checks passed."
                )
            ),
        ),
    ]

    return {
        "scenario_id": scenario["entity_id"],
        "scenario_name": scenario.get("scenario_name"),
        "status": status,
        "explanation": explanation,
        "expected_households": scenario.get("expected_households"),
        "warehouse_id": scenario.get("warehouse_id"),
        "references": {
            "vehicle_id": vehicle_id,
            "driver_id": driver_id,
            "site_id": site_id,
            "permit_id": permit_id,
        },
        "checks": checks,
        "evidence": {
            "dataset_version": scenario["dataset_version"],
            "data_classification": scenario["data_classification"],
            "ingested_at": scenario.get("ingested_at"),
        },
        "not_for_real_dispatch": True,
        "dispatch_enabled": False,
    }


def _inventory_identity(item: dict[str, Any]) -> str:
    return str(item.get("item_id") or item.get("sku") or item.get("item") or "").strip()


def _cold_chain_risk(item: dict[str, Any]) -> str | None:
    value = str(item.get("risk_status") or item.get("cold_chain_risk") or "").strip().lower()
    return value if value in KNOWN_COLD_CHAIN_RISKS else None


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _raw_quantity(item: dict[str, Any]) -> float:
    for key in ("on_hand", "quantity", "qty"):
        if item.get(key) is not None:
            return max(_as_float(item.get(key)), 0.0)
    return 0.0


def _unit_weight(item: dict[str, Any]) -> float:
    return max(_as_float(item.get("unit_weight_lbs", item.get("weight_lbs", 1)), 1.0), 0.01)


def _temperature_zone(item: dict[str, Any]) -> str:
    return str(item.get("temperature_zone") or item.get("storage_zone") or "ambient").strip().lower() or "ambient"


def _category_key(value: Any) -> str:
    return " ".join(str(value or "unclassified").strip().lower().replace("_", " ").replace("-", " ").split())


def _category_aliases(category: str) -> set[str]:
    normalized = _category_key(category)
    aliases = {normalized}
    if normalized in {"produce", "fresh produce", "fruit", "vegetable", "greens"}:
        aliases.update({"produce", "fresh produce", "fruit", "vegetable", "greens"})
    if normalized in {"protein", "bean", "beans", "legume", "legumes", "lentil", "lentils"}:
        aliases.update({"protein", "bean", "beans", "legume", "legumes", "lentil", "lentils"})
    if normalized in {"whole grain", "whole grains", "grain", "grains", "pantry", "shelf stable"}:
        aliases.update({"whole grain", "whole grains", "grain", "grains", "pantry", "shelf stable"})
    return aliases


def _nutrition_targets() -> dict[str, tuple[float, float]]:
    return {
        "fresh produce": (
            _as_float(os.getenv("NUTRITION_TARGET_PRODUCE_MIN"), 0.30),
            _as_float(os.getenv("NUTRITION_TARGET_PRODUCE_MAX"), 0.40),
        ),
        "protein": (
            _as_float(os.getenv("NUTRITION_TARGET_PROTEIN_MIN"), 0.15),
            _as_float(os.getenv("NUTRITION_TARGET_PROTEIN_MAX"), 0.25),
        ),
        "whole grain": (
            _as_float(os.getenv("NUTRITION_TARGET_WHOLE_GRAIN_MIN"), 0.10),
            _as_float(os.getenv("NUTRITION_TARGET_WHOLE_GRAIN_MAX"), 0.20),
        ),
    }


def _share_for_category(nutrition_mix: list[dict[str, Any]], category: str) -> float:
    aliases = _category_aliases(category)
    total = 0.0
    for row in nutrition_mix:
        if _category_key(row.get("nutritional_category")) in aliases:
            total += _as_float(row.get("share"))
    return total


def _nutrition_policy_compliance(load: dict[str, Any]) -> list[dict[str, Any]]:
    nutrition_mix = list(load.get("nutrition_mix") or [])
    rows = []
    for category, (minimum, maximum) in _nutrition_targets().items():
        share = _share_for_category(nutrition_mix, category)
        if minimum <= share <= maximum:
            status = "Ready"
            finding = "within target"
        elif share < minimum:
            status = "Partial"
            finding = "below target; likely supply or category constraint"
        else:
            status = "Partial"
            finding = "above target; review load balance"
        rows.append(
            {
                "category": category,
                "target_min": round(minimum, 4),
                "target_max": round(maximum, 4),
                "proposed_share": round(share, 4),
                "status": status,
                "finding": finding,
            }
        )
    return rows


def _warehouse_pick_list(load: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for index, item in enumerate(load.get("items") or [], 1):
        mode = str(item.get("distribution_mode") or "standard")
        spoilage = str(item.get("spoilage_status") or "unknown")
        rows.append(
            {
                "line": index,
                "item_id": item.get("item_id"),
                "item": item.get("item"),
                "qty": _as_int(item.get("qty")),
                "weight_lbs": round(_as_float(item.get("weight_lbs")), 2),
                "temperature_zone": _temperature_zone(item),
                "nutritional_category": item.get("nutritional_category") or item.get("category"),
                "distribution_mode": mode,
                "pick_priority": "rescue_first" if spoilage == "rescue" else ("use_soon" if spoilage == "use_soon" else "standard"),
                "human_approval_required": mode == "rescue_review",
            }
        )
    return rows


def _loading_order(pick_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zone_order = {"frozen": 0, "refrigerated": 1, "cold": 1, "ambient": 2}
    priority_order = {"rescue_first": 0, "use_soon": 1, "standard": 2}
    ordered = sorted(
        pick_list,
        key=lambda row: (
            zone_order.get(str(row.get("temperature_zone") or "ambient"), 3),
            priority_order.get(str(row.get("pick_priority") or "standard"), 3),
            str(row.get("item") or ""),
        ),
    )
    return [{**row, "load_sequence": index} for index, row in enumerate(ordered, 1)]


def _truck_manifest(route: dict[str, Any], load: dict[str, Any]) -> dict[str, Any]:
    items = list(load.get("items") or [])
    return {
        "stop_count": len(route.get("selected_stops") or []),
        "total_weight_lbs": round(sum(_as_float(item.get("weight_lbs")) for item in items), 2),
        "items": [
            {
                "item_id": item.get("item_id"),
                "item": item.get("item"),
                "qty": item.get("qty"),
                "weight_lbs": item.get("weight_lbs"),
                "distribution_mode": item.get("distribution_mode"),
                "allocations": item.get("allocations") or [],
            }
            for item in items
        ],
    }


def _inventory_remaining(on_hand: list[dict[str, Any]], load: dict[str, Any]) -> list[dict[str, Any]]:
    loaded_by_id = {
        _inventory_identity(item): _as_int(item.get("qty"))
        for item in load.get("items") or []
        if _inventory_identity(item)
    }
    rows = []
    for item in on_hand:
        item_id = _inventory_identity(item)
        original = _raw_quantity(item)
        loaded = loaded_by_id.get(item_id, 0)
        minimum_reserve = max(_as_float(item.get("minimum_reserve")), 0.0)
        remaining = max(original - loaded, 0.0)
        rows.append(
            {
                "item_id": item_id,
                "item": item.get("item") or item.get("name") or item.get("sku"),
                "starting_qty": original,
                "loaded_qty": loaded,
                "remaining_qty": remaining,
                "remaining_weight_lbs": round(remaining * _unit_weight(item), 2),
                "minimum_reserve": minimum_reserve,
                "low_stock": remaining <= minimum_reserve,
                "category": item.get("category"),
                "nutritional_category": item.get("nutritional_category"),
            }
        )
    return rows


def _restock_recommendations(
    remaining: list[dict[str, Any]], policy_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for row in remaining:
        if row.get("low_stock"):
            rows.append(
                {
                    "kind": "low_stock",
                    "item_id": row.get("item_id"),
                    "item": row.get("item"),
                    "reason": "Remaining quantity is at or below the configured minimum reserve.",
                    "human_review_required": True,
                }
            )
    for row in policy_rows:
        if row.get("status") == "Partial" and "below" in str(row.get("finding")):
            rows.append(
                {
                    "kind": "nutrition_gap",
                    "category": row.get("category"),
                    "reason": f"Proposed share {float(row.get('proposed_share') or 0):.0%} is below the policy target.",
                    "human_review_required": True,
                }
            )
    return rows[:12]


def _average_need(stops: list[dict[str, Any]]) -> float:
    values = [_as_float(stop.get("neighborhood_vulnerability_score", stop.get("need_score"))) for stop in stops]
    values = [value for value in values if value > 0]
    return round(sum(values) / len(values), 2) if values else 0.0


def _community_impact(route: dict[str, Any], load: dict[str, Any]) -> dict[str, Any]:
    household_plan = load.get("household_plan") or {}
    stops = list(route.get("selected_stops") or [])
    return {
        "planned_stop_count": len(stops),
        "average_scout_need_score": _average_need(stops),
        "represented_households": household_plan.get("represented_households"),
        "service_households_min": household_plan.get("service_households_min"),
        "service_households_max": household_plan.get("service_households_max"),
        "planned_weight_lbs": load.get("recommended_weight_lbs"),
        "rescue_candidate_weight_lbs": (load.get("spoilage_summary") or {}).get("rescue_candidate_weight_lbs"),
        "method": "household reach range plus Scout need score and Dispatch allocation plan",
    }


def _warehouse_impact(load: dict[str, Any], remaining: list[dict[str, Any]]) -> dict[str, Any]:
    low_stock = [row for row in remaining if row.get("low_stock")]
    return {
        "loaded_weight_lbs": load.get("recommended_weight_lbs"),
        "remaining_inventory_records": len(remaining),
        "low_stock_count": len(low_stock),
        "low_stock_items": low_stock[:10],
        "expected_spoilage_remaining_lbs": sum(
            _as_float(row.get("unallocated_at_risk_weight_lbs"))
            for row in load.get("rescue_recommendations") or []
        ),
    }


def _mission_risks(checks: list[dict[str, Any]], route: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for check in checks:
        status = str(check.get("status") or "Unknown")
        if status != "Ready":
            rows.append(
                {
                    "risk": check.get("check"),
                    "status": status,
                    "finding": check.get("finding"),
                }
            )
    travel_source = str(route.get("travel_time_source") or "")
    rows.append(
        {
            "risk": "traffic",
            "status": "Ready" if "road_network" in travel_source else "Unknown",
            "finding": f"Route timing source: {travel_source or 'unknown'}.",
        }
    )
    return rows


def _mission_scorecard(
    status: str,
    checks: list[dict[str, Any]],
    load: dict[str, Any],
    nutrition_policy: list[dict[str, Any]],
) -> dict[str, Any]:
    objective = load.get("dispatch_objective") or {}
    objective_score = min(max(_as_float(objective.get("average_score_per_unit")) * 100, 0.0), 100.0)
    ready = sum(1 for check in checks if check.get("status") == "Ready")
    partial = sum(1 for check in checks if check.get("status") == "Partial")
    readiness_score = round((ready + partial * 0.5) / len(checks) * 100, 1) if checks else 0.0
    nutrition_ready = sum(1 for row in nutrition_policy if row.get("status") == "Ready")
    nutrition_score = round(nutrition_ready / len(nutrition_policy) * 100, 1) if nutrition_policy else 100.0
    reserve_rows = list(load.get("stop_reserves") or [])
    reserve_score = round(
        sum(1 for row in reserve_rows if row.get("reserve_protected")) / len(reserve_rows) * 100,
        1,
    ) if reserve_rows else 0.0
    target = _as_float(load.get("target_load_lbs"))
    recommended = _as_float(load.get("recommended_weight_lbs"))
    capacity_score = round(min(recommended / target, 1.0) * 100, 1) if target > 0 else 0.0
    rescue_count = len(load.get("rescue_recommendations") or [])
    spoilage_score = 100.0 if rescue_count == 0 else 80.0
    mission_score = round(
        0.25 * readiness_score
        + 0.20 * objective_score
        + 0.20 * reserve_score
        + 0.15 * nutrition_score
        + 0.10 * capacity_score
        + 0.10 * spoilage_score,
        1,
    )
    return {
        "status": status,
        "mission_score": mission_score,
        "objective_score": round(objective_score, 1),
        "readiness_score": readiness_score,
        "equity_reserve_score": reserve_score,
        "nutrition_score": nutrition_score,
        "capacity_score": capacity_score,
        "spoilage_score": spoilage_score,
        "method": "weighted readiness, Dispatch objective, stop reserve, nutrition, capacity, and spoilage scores",
        "not_for_real_dispatch": True,
    }


def _optimization_explanation(
    route: dict[str, Any],
    load: dict[str, Any],
    community_intelligence: dict[str, Any],
    nutrition_policy: list[dict[str, Any]],
) -> list[str]:
    stops = list(route.get("selected_stops") or [])
    explanation = [
        "Dispatch used a deterministic Knapsack of Equity objective over selected items and stops; the LLM did not choose quantities.",
    ]
    if stops:
        highest = max(stops, key=lambda stop: _as_float(stop.get("neighborhood_vulnerability_score", stop.get("need_score"))))
        explanation.append(
            f"Stop {highest.get('sequence') or highest.get('stop_id')} received priority because Scout scored it highest among selected stops."
        )
    if load.get("all_stops_protected"):
        explanation.append("Every planned stop received a protected reserve before remaining units were allocated.")
    if load.get("rescue_recommendations"):
        explanation.append("Inventory inside the rescue window was prioritized and surfaced for human review before any donation or free-distribution action.")
    below = [row for row in nutrition_policy if row.get("status") == "Partial" and "below" in str(row.get("finding"))]
    for row in below[:2]:
        explanation.append(
            f"{row.get('category')} is below policy target because current selected inventory could not fill that nutrition band."
        )
    if community_intelligence.get("synthetic"):
        explanation.append("Community preference coefficients came from synthetic demo data because a verified request API/feed is not configured yet.")
    else:
        explanation.append("Community preference coefficients came from the configured community preference matrix.")
    return explanation


def run_mission_ops(
    route: dict[str, Any],
    load_lbs: float,
    time_window_hours: float,
    *,
    requested_categories: list[str] | None = None,
    excluded_categories: list[str] | None = None,
    inventory_store: S3InventoryStore | None = None,
    mission_id_factory=None,
) -> dict[str, Any]:
    """Draft an equitable, spoilage-aware route mission for human review."""
    started_at = perf_counter()
    if load_lbs <= 0 or time_window_hours <= 0:
        raise ValueError("load_lbs and time_window_hours must be positive")
    if route.get("status") != "optimal" or not route.get("selected_stops"):
        raise ValueError("Dispatch requires a viable route with at least one selected stop")
    store = inventory_store or S3InventoryStore()
    inventory_started_at = perf_counter()
    if hasattr(store, "read_many"):
        snapshot = store.read_many((ON_HAND_KEY, COLD_CHAIN_KEY))
        on_hand = snapshot[ON_HAND_KEY]
        cold_chain = snapshot[COLD_CHAIN_KEY]
    else:
        on_hand = store.read(ON_HAND_KEY)
        cold_chain = store.read(COLD_CHAIN_KEY)
    inventory_read_ms = max(0, round((perf_counter() - inventory_started_at) * 1000))
    route, community_intelligence = enrich_route_with_community_intelligence(route, store)
    cold_risk_by_id = {
        _inventory_identity(item): _cold_chain_risk(item)
        for item in cold_chain
        if _inventory_identity(item)
    }
    inventory = [
        {
            **item,
            "cold_chain_risk": cold_risk_by_id.get(_inventory_identity(item)) or "unknown",
        }
        for item in on_hand
    ]
    route_minutes = float(route.get("route_minutes") or 0)
    capacity_used = float(route.get("capacity_used") or 0)
    route_load_lbs = min(
        float(load_lbs),
        float(route.get("effective_route_load_lbs") or load_lbs),
    )
    load = build_load_recommendation(
        route,
        inventory,
        route_load_lbs,
        requested_categories=requested_categories,
        excluded_categories=excluded_categories,
    )
    recommended = float(load["recommended_weight_lbs"])
    household_target_lbs = float(load.get("target_load_lbs") or route_load_lbs)
    suggested_ids = {
        _inventory_identity(item) for item in load["items"] if _inventory_identity(item)
    }
    missing_cold_chain = sorted(
        item_id for item_id in suggested_ids if not cold_risk_by_id.get(item_id)
    )
    time_limit_minutes = time_window_hours * 60
    rescue_count = len(load.get("rescue_recommendations") or [])
    rescue_weight = float((load.get("spoilage_summary") or {}).get("rescue_candidate_weight_lbs") or 0)
    all_stops_protected = bool(load.get("all_stops_protected"))
    stop_count = len(route["selected_stops"])
    checks = [
        {
            "check": "route",
            "status": "Ready",
            "finding": f"Router produced {stop_count} viable stops using its current road-network constraints.",
            "data_used": ["Router selected_stops", "Router route status"],
        },
        {
            "check": "time_window",
            "status": "Ready" if route_minutes <= time_limit_minutes else "Blocked",
            "finding": f"Route requires {route_minutes:g} minutes against a {time_limit_minutes:g}-minute window.",
            "data_used": ["Router route_minutes", "Crew request time_window_hours"],
        },
        {
            "check": "vehicle_capacity",
            "status": "Ready" if capacity_used <= route_load_lbs else "Blocked",
            "finding": (
                f"Planned route load is {capacity_used:g} lbs against an inventory-aware "
                f"route limit of {route_load_lbs:g} lbs (requested: {load_lbs:g} lbs)."
            ),
            "data_used": ["Router capacity_used", "Router effective_route_load_lbs", "Crew request load_lbs"],
        },
        {
            "check": "inventory",
            "status": "Ready" if recommended >= household_target_lbs else "Partial",
            "finding": (
                f"On-hand inventory supports {recommended:g} lbs against the household-based "
                f"{household_target_lbs:g}-lb target (requested capacity: {load_lbs:g} lbs)."
            ),
            "data_used": [f"s3://{store.bucket}/{ON_HAND_KEY}", "Crew request load_lbs", "Selected-stop household range"],
        },
        {
            "check": "community_intelligence",
            "status": "Ready" if community_intelligence.get("source") != "unavailable" else "Unknown",
            "finding": (
                "Dispatch used a synthetic demo community preference matrix; replace with a verified request/API feed before real dispatch."
                if community_intelligence.get("synthetic")
                else "Dispatch used the configured community preference matrix."
            ),
            "data_used": [community_intelligence.get("source_key") or "synthetic community preference matrix"],
        },
        {
            "check": "equitable_reserves",
            "status": "Ready" if all_stops_protected else "Partial",
            "finding": (
                f"All {stop_count} planned stops have protected inventory reserves before departure."
                if all_stops_protected
                else "Inventory is insufficient to protect a positive reserve at every planned stop; review stop allocations before approval."
            ),
            "data_used": ["Scout need_score", "Selected-stop household demand", "Dispatch stop_reserves"],
        },
        {
            "check": "spoilage",
            "status": "Partial" if rescue_count else "Ready",
            "finding": (
                f"{rescue_weight:g} lbs of inventory is inside the rescue window; Dispatch generated {rescue_count} human-reviewable rescue recommendation(s)."
                if rescue_count
                else "No loaded inventory requires rescue-mode review under the configured spoilage window."
            ),
            "data_used": [f"s3://{store.bucket}/{ON_HAND_KEY}", "Dispatch days_to_spoil / expiration fields"],
        },
        {
            "check": "request_match",
            "status": "Ready" if load["category_match"] else "Partial",
            "finding": (
                f"Suggested items match requested categories ({', '.join(load['requested_categories'])}) and exclude prohibited categories ({', '.join(load['excluded_categories']) or 'none'})."
                if load["category_match"] and (load["requested_categories"] or load["excluded_categories"])
                else (
                    f"No on-hand items satisfied all requested categories ({', '.join(load['requested_categories']) or 'any'}) after exclusions ({', '.join(load['excluded_categories']) or 'none'})."
                    if (load["requested_categories"] or load["excluded_categories"])
                    else "No product category constraint was requested."
                )
            ),
            "data_used": ["Crew request", f"s3://{store.bucket}/{ON_HAND_KEY}"],
        },
        {
            "check": "cold_chain",
            "status": "Ready" if suggested_ids and not missing_cold_chain else "Unknown",
            "finding": (
                f"Every suggested item has a matching cold-chain risk record ({len(suggested_ids)} evaluated)."
                if suggested_ids and not missing_cold_chain
                else f"Missing cold-chain evidence for {len(missing_cold_chain)} suggested item(s): {', '.join(missing_cold_chain) or 'no suggested items to evaluate'}."
            ),
            "data_used": [f"s3://{store.bucket}/{COLD_CHAIN_KEY}"],
        },
    ]
    nutrition_policy = _nutrition_policy_compliance(load)
    remaining_inventory = _inventory_remaining(on_hand, load)
    pick_list = _warehouse_pick_list(load)
    loading_order = _loading_order(pick_list)
    blocked = any(check["status"] == "Blocked" for check in checks)
    partial = any(check["status"] in {"Partial", "Unknown"} for check in checks)
    status = "Blocked" if blocked else ("Partial" if partial else "Ready")
    scorecard = _mission_scorecard(status, checks, load, nutrition_policy)
    make_id = mission_id_factory or (lambda: f"mission-{uuid4().hex[:12]}")
    return {
        "mission_id": make_id(),
        "status": status,
        "route": route,
        "suggested_load": load["items"],
        "load_recommendation": load,
        "dispatch_objective": load.get("dispatch_objective") or {},
        "mission_scorecard": scorecard,
        "optimization_explanation": _optimization_explanation(route, load, community_intelligence, nutrition_policy),
        "stop_reserves": load.get("stop_reserves") or [],
        "nutrition_mix": load.get("nutrition_mix") or [],
        "nutrition_policy": nutrition_policy,
        "rescue_recommendations": load.get("rescue_recommendations") or [],
        "community_intelligence": community_intelligence,
        "warehouse_pick_list": pick_list,
        "truck_manifest": _truck_manifest(route, load),
        "loading_order": loading_order,
        "cold_chain_checklist": [
            row for row in pick_list if row.get("temperature_zone") in {"refrigerated", "cold", "frozen"} or row.get("pick_priority") == "rescue_first"
        ],
        "inventory_remaining": remaining_inventory,
        "restock_recommendations": _restock_recommendations(remaining_inventory, nutrition_policy),
        "community_impact": _community_impact(route, load),
        "warehouse_impact": _warehouse_impact(load, remaining_inventory),
        "mission_risks": _mission_risks(checks, route),
        "readiness_checks": checks,
        "performance": {
            "inventory_read_ms": inventory_read_ms,
            "dispatch_total_ms": max(0, round((perf_counter() - started_at) * 1000)),
        },
        "not_for_real_dispatch": True,
        "human_review_required": True,
        "dispatch_enabled": False,
    }
