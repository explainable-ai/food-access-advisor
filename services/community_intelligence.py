"""Community preference signals for LastMile Market mission planning.

This module deliberately treats community food preferences as an explicit data
source. It never infers cultural or dietary demand from race, ethnicity, or other
demographics. When a verified operator/community source is not configured yet, it
returns a clearly labelled synthetic-demo matrix so the Mission Optimization
Engine can demonstrate the workflow without pretending the data is real.
"""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

from storage.inventory import S3InventoryStore


COMMUNITY_PREFERENCES_KEY = os.getenv(
    "COMMUNITY_PREFERENCES_KEY", "community/preferences.json"
)
ALLOW_SYNTHETIC_FALLBACK = os.getenv(
    "COMMUNITY_INTELLIGENCE_ALLOW_SYNTHETIC_FALLBACK", "true"
).strip().lower() in {"1", "true", "yes", "on"}

_SYNTHETIC_COMMUNITY_PREFERENCES: list[dict[str, Any]] = [
    {
        "profile_id": "synthetic-austin-requests",
        "community_area": "Austin",
        "requested_categories": ["fresh produce", "protein", "whole grain"],
        "preference_tags": ["greens", "beans", "rice"],
        "request_count": 34,
        "confidence": 0.55,
    },
    {
        "profile_id": "synthetic-englewood-requests",
        "community_area": "Englewood",
        "requested_categories": ["fresh produce", "protein", "dairy"],
        "preference_tags": ["vegetables", "eggs", "fruit"],
        "request_count": 29,
        "confidence": 0.55,
    },
    {
        "profile_id": "synthetic-south-chicago-requests",
        "community_area": "South Chicago",
        "requested_categories": ["fresh produce", "whole grain", "protein"],
        "preference_tags": ["tomatoes", "peppers", "beans"],
        "request_count": 24,
        "confidence": 0.50,
    },
    {
        "profile_id": "synthetic-north-lawndale-requests",
        "community_area": "North Lawndale",
        "requested_categories": ["fresh produce", "pantry", "protein"],
        "preference_tags": ["fruit", "oatmeal", "lentils"],
        "request_count": 21,
        "confidence": 0.50,
    },
    {
        "profile_id": "synthetic-rural-fringe-requests",
        "region_name": "Chicagoland rural fringe",
        "requested_categories": ["fresh produce", "dairy", "shelf stable"],
        "preference_tags": ["milk", "potatoes", "rice"],
        "request_count": 18,
        "confidence": 0.45,
    },
]


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _synthetic_matrix(reason: str) -> dict[str, Any]:
    rows = deepcopy(_SYNTHETIC_COMMUNITY_PREFERENCES)
    return {
        "source": "synthetic_demo",
        "source_key": None,
        "data_classification": "synthetic_demo",
        "confidence": 0.45,
        "reason": reason,
        "rows": rows,
        "guardrail": (
            "Synthetic community preferences are placeholders. Replace with "
            "verified app, SMS, partner-pantry, or operator feedback before "
            "using for real dispatch decisions. Do not infer preferences from demographics."
        ),
    }


def load_community_preference_matrix(
    store: S3InventoryStore | None = None,
) -> dict[str, Any]:
    """Load a verified community preference matrix or a labelled demo fallback."""
    if os.getenv("COMMUNITY_INTELLIGENCE_MODE", "enabled").strip().lower() == "disabled":
        return {
            "source": "disabled",
            "source_key": None,
            "data_classification": "unavailable",
            "confidence": 0.0,
            "reason": "COMMUNITY_INTELLIGENCE_MODE=disabled",
            "rows": [],
            "guardrail": "Community preference matching is disabled.",
        }
    try:
        inventory_store = store or S3InventoryStore()
        rows = inventory_store.read(COMMUNITY_PREFERENCES_KEY)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("community preference object must be a JSON array of objects")
        return {
            "source": "s3",
            "source_key": COMMUNITY_PREFERENCES_KEY,
            "data_classification": "operator_or_community_provided",
            "confidence": 0.85,
            "reason": "Verified community preference matrix loaded from S3.",
            "rows": deepcopy(rows),
            "guardrail": (
                "Preferences must come from explicit community requests, partner feedback, "
                "or observed demand; do not infer preferences from demographics."
            ),
        }
    except Exception as exc:
        if not ALLOW_SYNTHETIC_FALLBACK:
            return {
                "source": "unavailable",
                "source_key": COMMUNITY_PREFERENCES_KEY,
                "data_classification": "unavailable",
                "confidence": 0.0,
                "reason": f"Community preference source unavailable: {exc}",
                "rows": [],
                "guardrail": "No community preference data was used.",
            }
        return _synthetic_matrix(
            f"No verified community preference source was readable at {COMMUNITY_PREFERENCES_KEY}: {exc}"
        )


def _profile_match(stop: dict[str, Any], profile: dict[str, Any]) -> tuple[int, str]:
    stop_tract = _normalize(stop.get("tract_fips") or stop.get("stop_id"))
    profile_tract = _normalize(profile.get("tract_fips") or profile.get("stop_id"))
    if stop_tract and profile_tract and stop_tract == profile_tract:
        return 100, "tract_fips"

    stop_area = _normalize(stop.get("community_area") or stop.get("name"))
    profile_area = _normalize(profile.get("community_area") or profile.get("name"))
    if stop_area and profile_area and (stop_area == profile_area or profile_area in stop_area):
        return 80, "community_area"

    stop_region = _normalize(stop.get("region_name") or stop.get("county_name") or stop.get("name"))
    profile_region = _normalize(profile.get("region_name") or profile.get("county_name"))
    if stop_region and profile_region and profile_region in stop_region:
        return 60, "region_name"
    return 0, "none"


def _best_profile(stop: dict[str, Any], matrix: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    best: tuple[int, str, dict[str, Any] | None] = (0, "none", None)
    for profile in matrix.get("rows") or []:
        if not isinstance(profile, dict):
            continue
        score, match_on = _profile_match(stop, profile)
        candidate = (score, match_on, profile)
        if score > best[0]:
            best = candidate
    return best[2], best[1]


def _default_profile(stop: dict[str, Any]) -> dict[str, Any]:
    """Fallback is a generic nutrition target, not a demographic inference."""
    return {
        "profile_id": "synthetic-generic-nutrition-request",
        "requested_categories": ["fresh produce", "protein", "whole grain"],
        "preference_tags": ["fresh produce", "protein", "whole grain"],
        "request_count": 0,
        "confidence": 0.35,
        "note": "Generic nutrition-balanced fallback; not based on demographics.",
    }


def enrich_stops_with_community_intelligence(
    stops: list[dict[str, Any]],
    matrix: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach explicit community preference coefficients to route stops."""
    preference_matrix = matrix or load_community_preference_matrix()
    is_synthetic_demo = preference_matrix.get("source") == "synthetic_demo"
    enriched: list[dict[str, Any]] = []
    matches = []
    for stop in stops:
        profile, matched_on = _best_profile(stop, preference_matrix)
        if profile is None:
            if preference_matrix.get("source") == "synthetic_demo":
                profile = _default_profile(stop)
                matched_on = "generic_nutrition_fallback"
            else:
                profile = {}
                matched_on = "none"
        requested = _text_list(profile.get("requested_categories"))
        tags = _text_list(profile.get("preference_tags"))
        enriched_stop = {
            **stop,
            "community_requested_categories": list(dict.fromkeys(_text_list(stop.get("community_requested_categories")) + requested)),
            "nutritional_priorities": list(dict.fromkeys(_text_list(stop.get("nutritional_priorities")) + requested)),
            "preference_tags": list(dict.fromkeys(_text_list(stop.get("preference_tags")) + tags)),
            "community_intelligence": {
                "source": preference_matrix.get("source"),
                "source_key": preference_matrix.get("source_key"),
                "data_classification": preference_matrix.get("data_classification"),
                "matched_on": matched_on,
                "profile_id": profile.get("profile_id"),
                "request_count": profile.get("request_count", 0),
                "confidence": profile.get("confidence", preference_matrix.get("confidence", 0.0)),
                "synthetic": is_synthetic_demo,
                "guardrail": preference_matrix.get("guardrail"),
            },
        }
        enriched.append(enriched_stop)
        matches.append(enriched_stop["community_intelligence"])
    summary = {
        "source": preference_matrix.get("source"),
        "source_key": preference_matrix.get("source_key"),
        "data_classification": preference_matrix.get("data_classification"),
        "stop_count": len(stops),
        "matched_stop_count": len(matches),
        "synthetic": is_synthetic_demo,
        "reason": preference_matrix.get("reason"),
        "guardrail": preference_matrix.get("guardrail"),
        "matches": matches,
    }
    return enriched, summary


def enrich_route_with_community_intelligence(
    route: dict[str, Any],
    store: S3InventoryStore | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a copy of a route whose selected stops include preference signals."""
    matrix = load_community_preference_matrix(store)
    stops, summary = enrich_stops_with_community_intelligence(
        list(route.get("selected_stops") or []), matrix
    )
    return {**route, "selected_stops": stops}, summary
