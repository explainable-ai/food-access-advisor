"""Non-persistent feedback calculation for the LastMile Market demo."""

from __future__ import annotations

from typing import Any

from tools.access_data import get_all_rural_tracts, get_all_tracts
from tools.gap_scorer import recompute_score_with_override, score_all_gaps
from tools.resource_cache import load_resource_cache


def _current_scored_tract(tract_id: str) -> dict[str, Any]:
    for scope, tracts in (
        ("urban", [row for row in get_all_tracts() if row.get("is_chicago")]),
        ("rural", get_all_rural_tracts()),
    ):
        if not any(str(row.get("tract_fips")) == tract_id for row in tracts):
            continue
        resources = load_resource_cache(scope, require_complete_coverage=True)
        return next(row for row in score_all_gaps(tracts, resources) if str(row.get("tract_fips")) == tract_id)
    raise LookupError(f"Tract {tract_id} is not in a configured study area")


def calculate_feedback(tract_id: str, households_served: int) -> dict[str, Any]:
    if households_served < 0:
        raise ValueError("households_served cannot be negative")
    before = _current_scored_tract(tract_id)
    households_total = before.get("households_total")
    if households_total in (None, 0):
        raise ValueError("Household coverage cannot be adjusted because households_total is unavailable")
    current = (before.get("score_components") or {}).get("existing_coverage")
    if current is None:
        raise ValueError("Existing coverage is unavailable for this tract")
    current_ratio = min(1.0, max(0.0, current / 100))
    served_share = min(1.0, households_served / float(households_total))
    # existing_coverage is a 0..1 access-quality proxy, not a household count.
    # Treat the served share as temporarily receiving full mobile-market
    # coverage, while the remainder retains its current access quality.
    adjusted = current_ratio + served_share * (1.0 - current_ratio)
    after = recompute_score_with_override(before, existing_coverage=adjusted)
    return {
        "tract_id": tract_id,
        "before": {"score": before["need_score"], "score_components": before["score_components"], "score_contributions": before["score_contributions"], "contributions": before["contributions"]},
        "after": after,
        "households_served": households_served,
        "persisted": False,
        "method": "coverage = current coverage + served household share × uncovered share",
        "note": "Illustrative recalculation only; prepared evidence and future Scout runs are unchanged.",
    }
