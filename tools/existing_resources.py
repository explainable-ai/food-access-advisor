"""Existing-resources tool: what's already there.

Queries OpenStreetMap's live Overpass API (public, no API key) for
community gardens, farms, grocery stores/supermarkets, and convenience
stores near a bounding box.

NOTE on SNAP retailer data: an earlier version of this tool also read a
manually-downloaded "USDA SNAP Retailer Locator" CSV of individual retailer
points. That standalone locator has been discontinued — USDA folded SNAP
retailer accessibility into the Food Access Research Atlas itself, as two
new tract-level aggregated products: SRAM (SNAP-authorized Retailer Access
Map, includes convenience/dollar stores, 2025 data) and LRAM (Large
Retailer Access Map, large grocery/supermarkets only, 2019 data, the
methodological continuation of the classic Atlas). Neither publishes
individual retailer coordinates — both are pre-aggregated to census tracts,
same as the main Atlas file. That means SRAM/LRAM belong in
`tools/access_data.py` / `data/prep_atlas.py` as an additional severity
signal, not here as point locations — see the README's Data setup section.
This tool's job is narrower now: OSM is the only point-level source left in
this project, and it's a real, working, live one.
"""

import logging
import time

import requests
from strands import tool

from config import PILOT_CITY, PILOT_RURAL_COUNTY

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 3

# Overpass's usage policy asks API consumers to identify themselves
# (https://dev.overpass-api.de/overpass-doc/en/preface/commons.html);
# requests' bare default ("python-requests/x.y.z") was getting a 406 from
# this mirror even though the identical query worked fine via curl on the
# same network — a real project identifier, not the generic default,
# resolved it.
REQUEST_HEADERS = {
    "User-Agent": "food-access-advisor/1.0 (Agents for Humans hackathon; "
    "https://github.com/explainable-ai/food-access-advisor)"
}

# The original six point-resource tags used in both study areas.
BASE_NODE_FILTERS = [
    'node["leisure"="garden"]',
    'way["landuse"="allotments"]',
    'node["shop"="farm"]',
    'node["shop"="grocery"]',
    'node["shop"="supermarket"]',
    'node["shop"="convenience"]',
]

URBAN_NODE_FILTERS = BASE_NODE_FILTERS + [
    'node["social_facility"="food_bank"]',
    'way["social_facility"="food_bank"]',
    'relation["social_facility"="food_bank"]',
    'node["amenity"="marketplace"]',
    'way["amenity"="marketplace"]',
    'relation["amenity"="marketplace"]',
]

# Keep the Route Advisor's existing eight-filter query unchanged and
# independent from future urban-only additions.
RURAL_NODE_FILTERS = BASE_NODE_FILTERS + [
    'node["social_facility"="food_bank"]',
    'node["amenity"="marketplace"]',
]

logger = logging.getLogger(__name__)


class OverpassQueryError(RuntimeError):
    """Raised when the live Overpass query could not be completed after
    retrying. Deliberately NOT caught here and turned into an empty list —
    see the note on _query_overpass for why that used to be a real bug."""


@tool
def get_existing_resources() -> list:
    """Find existing food resources near the pilot city via OpenStreetMap.

    Takes no arguments on purpose. An earlier version accepted south/west/
    north/east bounding-box floats, overridable by the caller — which meant
    the model could point a live query at any bounding box on Earth, not
    just the pilot city, with nothing in the code stopping it (the
    "stay within the pilot city" rule lived only in the system prompt).
    That's the same class of gap as the one fixed in
    `tools/access_data.py`'s `get_low_access_tracts`: a boundary that's
    real in the prompt but not in the code isn't a boundary. The bounding
    box now always comes from config.PILOT_CITY["bbox"] — to query a
    different area, change the config, not a tool argument.

    Returns:
        A list of dicts with stable `entity_id`, `kind` ("garden" |
        "grocery" | "farm" | "convenience" | "food_bank" | "market"),
        `name`, `lat`, `lon`.
        "convenience" is kept separate
        from "grocery" deliberately — a corner store or dollar store is a
        weaker food-access signal than a real grocery store (the "food
        swamp" pattern), and `score_gaps` should be free to weight them
        differently rather than have that distinction erased here.

        Straight-line distance is computed later in `score_gaps`; this does
        not yet include transit time — wiring in a GTFS-based routing
        lookup is the next planned upgrade (see the tracker notes), and
        `distance_miles` should be read as an approximation until then.

    Raises:
        OverpassQueryError: if the live query fails after retrying. This
            used to fail silently — a bare `print()` plus an empty-list
            return — which meant a network blip or an Overpass outage was
            indistinguishable from "genuinely zero resources near this
            city," and both the Advisor's scorer and the Watchdog's recheck
            would have confidently treated a service outage as confirmed
            fact (every tract scored as maximally underserved; every
            flagged tract reported as "still needed" during an outage).
            Strands catches tool exceptions and surfaces them to the model
            as a tool error rather than crashing the run, so raising here
            is safe and lets the agent (or a human) know the data is
            missing rather than quietly acting on a wrong assumption.
    """
    south, west, north, east = PILOT_CITY["bbox"]
    return _query_overpass(south, west, north, east, URBAN_NODE_FILTERS)


@tool
def get_rural_existing_resources() -> list:
    """Find existing food resources in the configured rural planning bands.

    Same boundary discipline as `get_existing_resources`: no arguments.
    Bounds always come from `config.PILOT_RURAL_COUNTY["resource_areas"]`
    (or its single `bbox` fallback). It uses the same verified resource tags
    as the urban snapshot — see RURAL_NODE_FILTERS.

    Returns:
        Same shape as `get_existing_resources`.
    """
    areas = PILOT_RURAL_COUNTY.get("resource_areas") or [
        {"name": "rural", "bbox": PILOT_RURAL_COUNTY["bbox"]}
    ]
    resources_by_id = {}
    for area in areas:
        south, west, north, east = area["bbox"]
        for resource in _query_overpass(
            south, west, north, east, RURAL_NODE_FILTERS
        ):
            resources_by_id[resource["entity_id"]] = resource
    return list(resources_by_id.values())


def _query_overpass(south, west, north, east, node_filters) -> list:
    filter_lines = "\n      ".join(
        f"{f}({south},{west},{north},{east});" for f in node_filters
    )
    query = f"""
    [out:json][timeout:25];
    (
      {filter_lines}
    );
    out center;
    """
    last_exc = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                headers=REQUEST_HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            break
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(
                "Overpass query failed (attempt %d/%d): %s",
                attempt, MAX_ATTEMPTS, exc, exc_info=attempt == MAX_ATTEMPTS,
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
    else:
        raise OverpassQueryError(
            f"Overpass query failed after {MAX_ATTEMPTS} attempt(s): {last_exc}"
        ) from last_exc

    out = []
    for el in elements:
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        shop = tags.get("shop")
        if tags.get("social_facility") == "food_bank":
            kind = "food_bank"
        elif tags.get("amenity") == "marketplace":
            kind = "market"
        elif "leisure" in tags or "landuse" in tags:
            kind = "garden"
        elif shop in ("grocery", "supermarket"):
            kind = "grocery"
        elif shop == "convenience":
            kind = "convenience"
        else:
            kind = "farm"
        out.append(
            {
                "entity_id": f"osm:{el.get('type', 'element')}/{el['id']}",
                "kind": kind,
                "name": tags.get("name", "(unnamed)"),
                "lat": lat,
                "lon": lon,
            }
        )
    return out
