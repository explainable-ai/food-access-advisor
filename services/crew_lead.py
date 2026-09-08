"""Crew Lead workflow for the Last Mile Crew.

The workflow is code-defined so order, stop conditions, and the response
contract are deterministic. Each capability is also exposed as a Strands tool
for the conversational Crew Lead agent and AgentCore deployment.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from strands import Agent, tool

from model import build_model
from services.load_recommendation import recommend_load
from storage.s3_inventory import S3InventoryStore
from tools.access_data import get_all_tracts, get_low_access_rural_tracts
from tools.evidence_snapshots import SOURCE_HEALTH_CHANGE_TYPES, read_change_page
from tools.gap_scorer import score_gaps
from tools.resource_cache import load_resource_cache
from tools.route_optimizer import optimize_route


STUDY_AREAS = {"chicago_neighborhoods", "rural_fringe"}
RURAL_HINTS = {
    "rural", "fringe", "kane", "kendall", "grundy", "will county",
    "kankakee", "mchenry", "harvard", "marengo", "sandwich", "manteno",
}
DEFAULT_HUB = {
    "name": "LastMile Market Chicago hub",
    "lat": float(os.getenv("LASTMILE_HUB_LAT", "41.8185")),
    "lon": float(os.getenv("LASTMILE_HUB_LON", "-87.7266")),
}


def _source_name(component: str, tract: dict[str, Any]) -> str:
    sources = tract.get("evidence_sources") or {}
    if component == "poverty" and tract.get("scoring_context_version"):
        return (sources.get("food_insecurity") or {}).get("name") or "Greater Chicago Food Depository Community Data Map, ACS 2024"
    if component == "transit_burden" and tract.get("scoring_context_version"):
        return (sources.get("transportation") or {}).get("name") or "Chicago Transit Authority static GTFS"
    if component in {"poverty", "no_vehicle", "population_served"}:
        return "U.S. Census Bureau ACS 5-year 2024"
    if component == "food_access_gap":
        return "USDA Food Access Research Atlas (SRAM 2025)"
    return "LastMile Market prepared public-resource snapshot"


def explain_tract(tract: dict[str, Any]) -> dict[str, Any]:
    """Expose every score component, including explicit nulls."""
    components = tract.get("score_components") or {}
    contributions = tract.get("score_contributions") or {}
    weights = tract.get("weights_used") or {}
    return {
        **tract,
        "score": tract.get("need_score"),
        "contributions": [
            {
                "component": component,
                "weight": weights.get(component),
                "raw_value": components.get(component),
                "contribution": contributions.get(component),
                "source": _source_name(component, tract),
            }
            for component in (
                "food_access_gap", "poverty", "no_vehicle", "transit_burden",
                "population_served", "existing_coverage",
            )
        ],
    }


def resolve_study_area(request: str, study_area: str | None) -> tuple[str, bool]:
    if study_area is not None:
        if study_area not in STUDY_AREAS:
            raise ValueError(f"study_area must be one of {sorted(STUDY_AREAS)} or null")
        return study_area, False
    lowered = request.lower()
    if any(hint in lowered for hint in RURAL_HINTS):
        return "rural_fringe", True
    return "chicago_neighborhoods", True


def parse_constraints(request: str, *, load_lbs: float | None = None,
                      time_window_hours: float | None = None) -> tuple[float, float]:
    if load_lbs is None:
        match = re.search(r"([\d,]+(?:\.\d+)?)\s*(?:lb|lbs|pounds?)\b", request, re.I)
        load_lbs = float(match.group(1).replace(",", "")) if match else None
    if time_window_hours is None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|hr)\b", request, re.I)
        time_window_hours = float(match.group(1)) if match else None
    missing = []
    if load_lbs is None:
        missing.append("load in pounds")
    if time_window_hours is None:
        missing.append("time window in hours")
    if missing:
        raise ValueError("Mission request must specify " + " and ".join(missing))
    if load_lbs <= 0 or time_window_hours <= 0:
        raise ValueError("Load and time window must be greater than zero")
    return float(load_lbs), float(time_window_hours)


def _in_scope(finding: dict[str, Any], study_area: str) -> bool:
    scope = str(finding.get("scope") or finding.get("source_scope") or "").lower()
    return ("rural" in scope) if study_area == "rural_fringe" else ("rural" not in scope)


def _run_sentry(study_area: str) -> dict[str, Any]:
    page = read_change_page(limit=100, status="open")
    findings = [item for item in page.get("items", []) if _in_scope(item, study_area)]
    source_health = [item for item in findings if item.get("change_type") in SOURCE_HEALTH_CHANGE_TYPES]
    operational = [item for item in findings if item.get("change_type") not in SOURCE_HEALTH_CHANGE_TYPES]
    return {
        "study_area": study_area,
        "operational_findings": operational,
        "source_health_alerts": source_health,
        "operational_finding_count": len(operational),
        "source_health_alert_count": len(source_health),
    }


@tool
def sentry_check(study_area: str) -> dict:
    """Read current Sentry findings before a route is committed."""
    return _run_sentry(study_area)


def _run_scout(study_area: str, scenario: str, area_was_inferred: bool,
               top_n: int, sentry_context: dict[str, Any] | None = None) -> dict[str, Any]:
    if study_area == "chicago_neighborhoods":
        tracts = [tract for tract in get_all_tracts() if tract.get("is_chicago")]
        resources = load_resource_cache("urban", require_complete_coverage=True)
    else:
        tracts = get_low_access_rural_tracts(limit=100)
        resources = load_resource_cache("rural", require_complete_coverage=True)
    ranked = [explain_tract(row) for row in score_gaps(tracts, resources, top_n=top_n)]
    note = f"Assumed study area: {study_area} (not specified in request). " if area_was_inferred else ""
    return {
        "study_area": study_area,
        "scenario": scenario,
        "area_was_inferred": area_was_inferred,
        "sentry_context": sentry_context or {},
        "summary": f"{note}Ranked {len(tracts)} tracts; top {len(ranked)} selected.",
        "ranked_tracts": ranked,
    }


@tool
def scout(study_area: str, scenario: str, area_was_inferred: bool = False,
          top_n: int = 5, sentry_context: dict | None = None) -> dict:
    """Rank tracts and return source-attributed score contributions."""
    return _run_scout(study_area, scenario, area_was_inferred, top_n, sentry_context)


def _route_candidates(top_tracts: list[dict[str, Any]], load_lbs: float) -> list[dict[str, Any]]:
    populations = [max(float(row.get("population") or 0), 0) for row in top_tracts]
    total = sum(populations)
    shares = [value / total for value in populations] if total else [1 / len(top_tracts)] * len(top_tracts)
    return [
        {
            "stop_id": str(row.get("tract_fips")),
            "tract_fips": str(row.get("tract_fips")),
            "lat": row.get("centroid_lat"),
            "lon": row.get("centroid_lon"),
            "demand": round(load_lbs * shares[index], 2),
            "need_score": row.get("need_score", row.get("score", 0)),
            "population": row.get("population"),
            "currently_served": False,
        }
        for index, row in enumerate(top_tracts)
    ]


def _run_router(top_tracts: list, hub: dict, time_window_hours: float,
                load_lbs: float, vehicle_capacity_lbs: float, max_stops: int) -> dict[str, Any]:
    candidates = _route_candidates(top_tracts[:max_stops], load_lbs)
    route = optimize_route(
        candidates=candidates,
        depot={"lat": hub["lat"], "lon": hub["lon"]},
        max_route_minutes=time_window_hours * 60,
        vehicle_capacity=vehicle_capacity_lbs,
        max_stops=max_stops,
        service_minutes=20,
        average_speed_mph=25,
    )
    return {"hub": hub, "route": route}


@tool
def router(top_tracts: list, hub: dict, time_window_hours: float,
           load_lbs: float, vehicle_capacity_lbs: float, max_stops: int = 5) -> dict:
    """Build the constrained route selected by Scout's tract ranking."""
    return _run_router(top_tracts, hub, time_window_hours, load_lbs, vehicle_capacity_lbs, max_stops)


def _run_dispatch(route: dict, load_lbs: float, time_window_hours: float,
                  vehicle_capacity_lbs: float, store: S3InventoryStore) -> dict[str, Any]:
    inventory = store.on_hand()
    cold_chain = store.cold_chain()
    planned_route_load = min(float(load_lbs), float(route.get("capacity_used") or load_lbs))
    load = recommend_load(
        route=route,
        inventory=inventory,
        cold_chain=cold_chain,
        vehicle_capacity_lbs=vehicle_capacity_lbs,
        requested_load_lbs=planned_route_load,
    )
    load["mission_request_lbs"] = round(float(load_lbs), 1)
    cold_counts = {"ok": 0, "at_risk": 0, "hold": 0}
    for lot in cold_chain:
        key = str(lot.get("status", "")).lower().replace(" ", "_")
        if key in cold_counts:
            cold_counts[key] += 1
    checks = [
        {
            "check": "vehicle_capacity", "status": "ok" if route.get("capacity_used", 0) <= vehicle_capacity_lbs else "failed",
            "found": {"capacity_lbs": vehicle_capacity_lbs, "planned_route_load_lbs": route.get("capacity_used")},
            "source": "Router constrained-route result",
        },
        {
            "check": "inventory", "status": "ok" if load["unfilled_request_lbs"] == 0 else "partial",
            "found": {"assigned_load_lbs": load["assigned_load_lbs"], "unfilled_request_lbs": load["unfilled_request_lbs"]},
            "source": "S3 inventory/on-hand.json",
        },
        {
            "check": "cold_chain", "status": "ok" if not cold_counts["at_risk"] and not cold_counts["hold"] else "partial",
            "found": cold_counts,
            "source": "S3 inventory/cold-chain.json",
        },
        {
            "check": "time_window", "status": "ok" if route.get("route_minutes", float("inf")) <= time_window_hours * 60 else "failed",
            "found": {"route_minutes": route.get("route_minutes"), "limit_minutes": time_window_hours * 60},
            "source": "Router constrained-route result",
        },
    ]
    return {"route": route, "suggested_load": load, "readiness_checks": checks}


@tool
def dispatch(route: dict, load_lbs: float, time_window_hours: float,
             vehicle_capacity_lbs: float) -> dict:
    """Draft a mission and explain capacity, inventory, and cold-chain checks."""
    return _run_dispatch(route, load_lbs, time_window_hours, vehicle_capacity_lbs, S3InventoryStore())


CREW_LEAD_PROMPT = """You are the Crew Lead for LastMile Market. Extract the requested load in
pounds and time window in hours. Use only chicago_neighborhoods or rural_fringe. When the caller
does not specify a study area, infer it from the request or default to chicago_neighborhoods and
make that assumption the first line of Scout's summary. Call the Last Mile Crew tools in exactly
this order: sentry_check, scout, router, dispatch, passing the relevant output forward. Never
continue after a failed or no-result step. Never invent missing evidence. Return a structured
step log and decision support for human review, never an autonomous dispatch decision."""


def build_crew_lead() -> Agent:
    """Return the conversational Strands orchestrator over the same four tools."""
    return Agent(model=build_model(), system_prompt=CREW_LEAD_PROMPT,
                 tools=[sentry_check, scout, router, dispatch])


def _failed(steps: list[dict[str, Any]], agent: str, status: str, reason: str) -> dict[str, Any]:
    steps.append({"agent": agent, "status": status, "summary": reason, "data": {}})
    return {"steps": steps, "mission_id": None, "final_summary": reason}


def run_crew_brief(
    request: str,
    *,
    study_area: str | None = None,
    scenario: str = "community_equity",
    load_lbs: float | None = None,
    time_window_hours: float | None = None,
    vehicle_capacity_lbs: float | None = None,
    hub: dict[str, Any] | None = None,
    max_stops: int = 5,
    inventory_store: S3InventoryStore | None = None,
) -> dict[str, Any]:
    """Execute the fixed Sentry → Scout → Router → Dispatch production workflow."""
    if not request or not request.strip():
        raise ValueError("request must be a non-empty string")
    area, inferred = resolve_study_area(request, study_area)
    requested_load, hours = parse_constraints(
        request, load_lbs=load_lbs, time_window_hours=time_window_hours
    )
    capacity = float(vehicle_capacity_lbs or requested_load)
    if capacity <= 0 or max_stops < 1 or max_stops > 15:
        raise ValueError("vehicle capacity must be positive and max_stops must be between 1 and 15")
    selected_hub = {**DEFAULT_HUB, **(hub or {})}
    steps: list[dict[str, Any]] = []

    try:
        sentry = _run_sentry(area)
    except Exception as exc:
        return _failed(steps, "sentry", "failed", f"Sentry could not read current findings: {exc}")
    steps.append({
        "agent": "sentry", "status": "ok",
        "summary": f"Sentry found {sentry['operational_finding_count']} operational change(s) and {sentry['source_health_alert_count']} source-health alert(s).",
        "data": sentry,
    })

    try:
        scouted = _run_scout(area, scenario, inferred, max_stops, sentry)
    except Exception as exc:
        return _failed(steps, "scout", "failed", f"Scout could not rank the study area: {exc}")
    if not scouted["ranked_tracts"]:
        return _failed(steps, "scout", "no_result", "Scout found no viable tracts in the selected study area.")
    steps.append({"agent": "scout", "status": "ok", "summary": scouted["summary"], "data": scouted})

    try:
        routed = _run_router(scouted["ranked_tracts"], selected_hub, hours, requested_load, capacity, max_stops)
    except Exception as exc:
        return _failed(steps, "router", "failed", f"Router could not build the route: {exc}")
    route = routed["route"]
    if route.get("status") != "optimal":
        return _failed(steps, "router", "no_result", f"Router found no viable route: {route.get('reason', 'constraints could not be met')}")
    steps.append({
        "agent": "router", "status": "ok",
        "summary": f"Route built: {len(route['selected_stops'])} stops, {route['route_minutes']} minutes, within capacity.",
        "data": routed,
    })

    try:
        dispatched = _run_dispatch(route, requested_load, hours, capacity, inventory_store or S3InventoryStore())
    except Exception as exc:
        return _failed(steps, "dispatch", "failed", f"Dispatch could not read readiness evidence: {exc}")
    if dispatched["suggested_load"]["assigned_load_lbs"] <= 0:
        return _failed(steps, "dispatch", "no_result", "Dispatch found no loadable inventory; enter or clear inventory before review.")
    mission_id = f"LM-{datetime.now(timezone.utc):%Y%m%d}-{uuid4().hex[:8].upper()}"
    dispatched["mission_id"] = mission_id
    steps.append({
        "agent": "dispatch", "status": "ok",
        "summary": "Mission drafted with a suggested load — ready for human review.",
        "data": dispatched,
    })
    return {
        "steps": steps,
        "mission_id": mission_id,
        "final_summary": f"{mission_id}: {len(route['selected_stops'])}-stop mission drafted for human review.",
    }
