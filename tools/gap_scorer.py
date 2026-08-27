"""Deterministic gap-scoring tool.

Deliberately NOT an LLM call: for a civic-data tool whose output might end up
in a funding application, "here's the exact formula" is more defensible than
"the model said so." Combines each tract's population, its access-severity
flags from the USDA Food Access Research Atlas, and the distance to the
nearest existing resource into a single, explainable need score.
"""

from strands import tool

from tools.geo import haversine_miles


def _severity_weight(tract: dict) -> float:
    """Low access at both the half-mile and one-mile urban thresholds is a
    stronger signal than low access at only the wider threshold."""
    if tract.get("low_access_half_mile"):
        return 1.0
    if tract.get("low_access_one_mile"):
        return 0.6
    return 0.2


def _nearest_resource(tract: dict, resources: list):
    # A tract with no centroid (real Atlas downloads don't always ship
    # lat/lon — see data/prep_atlas.py) can't have a distance computed to
    # anything. Treated the same as "no resources at all" rather than
    # crashing on radians(None) — the tract still gets ranked, just
    # without a distance signal, instead of taking down the whole run.
    if not resources or tract.get("centroid_lat") is None or tract.get("centroid_lon") is None:
        return None
    best, best_dist = None, float("inf")
    for res in resources:
        if res.get("lat") is None or res.get("lon") is None:
            continue
        dist = haversine_miles(
            tract["centroid_lat"], tract["centroid_lon"], res["lat"], res["lon"]
        )
        if dist < best_dist:
            best_dist, best = dist, {**res, "distance_miles": round(dist, 2)}
    return best


def _distance_component(nearest) -> float:
    """Transit time is the honest measure when we have it (see README on the
    GTFS upgrade); straight-line miles is the fallback until that's wired in.

    A nearest resource of kind "convenience" is discounted rather than
    treated as equivalent to a real grocery store — a corner store or
    dollar store nearby doesn't close the same gap a supermarket does (the
    "food swamp" pattern from the research this project is built on)."""
    if nearest is None:
        return 1.0

    if nearest.get("transit_minutes") is not None:
        raw = min(nearest["transit_minutes"] / 45, 1.0)
    else:
        raw = min(nearest.get("distance_miles", 0) / 3, 1.0)

    if nearest.get("kind") == "convenience":
        return min(raw + 0.35, 1.0)  # still counts as *something* nearby, just weaker
    return raw


@tool
def score_gaps(tracts: list, resources: list, top_n: int = 3) -> list:
    """Rank candidate tracts by unmet food-access need.

    Args:
        tracts: Low-income, low-access tracts from `get_low_access_tracts`,
            each a dict with at least `tract_fips`, `population`,
            `low_access_half_mile`, `low_access_one_mile`, `centroid_lat`,
            `centroid_lon`.
        resources: Existing food resources from `get_existing_resources`,
            each a dict with `lat`, `lon`, `kind`, and optionally
            `transit_minutes`.
        top_n: How many ranked tracts to return.

    Returns:
        The top_n tracts, each with an added `need_score` (0-100) and
        nearest-resource distance, sorted by need_score descending —
        highest unmet need first. This is the only place ranking happens;
        callers should treat this ranking as authoritative rather than
        re-deriving it.
    """
    scored = []
    for tract in tracts:
        nearest = _nearest_resource(tract, resources)
        weight = _severity_weight(tract)
        distance_component = _distance_component(nearest)
        population_component = min(tract.get("population", 0) / 5000, 1.0)

        need_score = round(
            100
            * (
                0.45 * weight
                + 0.35 * distance_component
                + 0.20 * population_component
            ),
            1,
        )

        entry = dict(tract)
        entry["need_score"] = need_score
        entry["nearest_resource_kind"] = nearest.get("kind") if nearest else None
        entry["nearest_resource_miles"] = nearest.get("distance_miles") if nearest else None
        entry["nearest_resource_minutes"] = nearest.get("transit_minutes") if nearest else None
        scored.append(entry)

    scored.sort(key=lambda t: t["need_score"], reverse=True)
    return scored[:top_n]
