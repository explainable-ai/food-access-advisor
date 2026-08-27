"""Recheck-scoring tool: has anything changed near a flagged tract?

Deliberately NOT an LLM call, same rationale as `gap_scorer.py` — whether a
resource has "appeared" near a tract should come from an explainable
distance threshold against live OSM data, not a model's impression of the
results. The Watchdog's only job is to read this verdict and decide what to
write back via `update_flagged_tract`; it should not be re-deriving this
itself from raw coordinates.
"""

try:
    from strands import tool
except ImportError:  # pragma: no cover - optional runtime dependency in local tests.
    def tool(func=None, **_kwargs):
        if func is None:
            return lambda f: f
        return func

from tools.geo import haversine_miles

NEARBY_THRESHOLD_MILES = 1.0


@tool
def check_resource_appeared(
    centroid_lat: float,
    centroid_lon: float,
    resources: list,
    threshold_miles: float = NEARBY_THRESHOLD_MILES,
) -> dict:
    """Check whether any resource in a live OSM query now sits near a tract.

    Args:
        centroid_lat, centroid_lon: The flagged tract's centroid — carried
            on the flagged-tracts row, not re-looked-up here.
        resources: Current output of `get_existing_resources` (live OSM
            query) — always freshly queried, never cached, so a resource
            that opened since the tract was flagged actually shows up.
        threshold_miles: Distance under which a resource counts as "closing
            the gap." Same urban-scale threshold used implicitly by the
            Atlas's half-mile/one-mile flags this project already relies on
            — not re-litigated per call.

    Returns:
        A dict: `resource_now_nearby` (bool), `nearest_kind`,
        `nearest_distance_miles` — the same shape whether or not anything
        was found, so the caller never has to branch on missing keys.
    """
    best_kind, best_dist = None, float("inf")
    for res in resources:
        if res.get("lat") is None or res.get("lon") is None:
            continue
        dist = haversine_miles(centroid_lat, centroid_lon, res["lat"], res["lon"])
        if dist < best_dist:
            best_dist, best_kind = dist, res.get("kind")

    if best_kind is None:
        return {"resource_now_nearby": False, "nearest_kind": None, "nearest_distance_miles": None}

    return {
        "resource_now_nearby": best_dist <= threshold_miles,
        "nearest_kind": best_kind,
        "nearest_distance_miles": round(best_dist, 2),
    }
