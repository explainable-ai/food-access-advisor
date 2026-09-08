"""Non-persistent, explicitly illustrative mission feedback calculation."""

from __future__ import annotations

from typing import Any

from tools.access_data import get_all_rural_tracts, get_all_tracts
from tools.gap_scorer import recompute_score_from_components, score_all_gaps
from tools.resource_cache import load_resource_cache


def _scored_tract(tract_id: str) -> dict[str, Any]:
    for scope, reader in (("urban", get_all_tracts), ("rural", get_all_rural_tracts)):
        tracts = reader()
        match = next((row for row in tracts if str(row.get("tract_fips")) == tract_id), None)
        if match is None:
            continue
        resources = load_resource_cache(scope, require_complete_coverage=True)
        return next(row for row in score_all_gaps(tracts, resources) if str(row["tract_fips"]) == tract_id)
    raise ValueError(f"tract_id {tract_id!r} is not in either configured study area")


def compute_demo_feedback(tract_id: str, households_served: int,
                          *, scored_tract: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return real-formula before/after math without persisting the override."""
    if households_served < 0:
        raise ValueError("households_served cannot be negative")
    tract = scored_tract or _scored_tract(str(tract_id))
    components = dict(tract.get("score_components") or {})
    current_coverage = components.get("existing_coverage")
    if current_coverage is None:
        adjusted_coverage = None
        bump = None
    else:
        households_total = tract.get("households_total")
        if not households_total or float(households_total) <= 0:
            raise ValueError("household denominator is unavailable for this tract")
        bump = min(100.0, households_served / float(households_total) * 100.0)
        adjusted_coverage = min(100.0, float(current_coverage) + bump)
    adjusted = {**components, "existing_coverage": adjusted_coverage}
    after, after_contributions = recompute_score_from_components(
        adjusted, tract.get("weights_used"), preserve_missing=bool(tract.get("scoring_context_version"))
    )
    return {
        "tract_id": str(tract_id),
        "before": tract.get("need_score"),
        "after": after,
        "households_served": households_served,
        "existing_coverage_before": current_coverage,
        "existing_coverage_after": None if adjusted_coverage is None else round(adjusted_coverage, 1),
        "coverage_bump": None if bump is None else round(bump, 1),
        "after_contributions": after_contributions,
        "formula": "production weighted score with an illustrative existing_coverage override",
        "illustrative_only": True,
        "persisted": False,
    }
