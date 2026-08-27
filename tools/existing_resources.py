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

import requests
from strands import tool

from config import PILOT_CITY

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


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
        A list of dicts with `kind` ("garden" | "grocery" | "farm" |
        "convenience"), `name`, `lat`, `lon`. "convenience" is kept separate
        from "grocery" deliberately — a corner store or dollar store is a
        weaker food-access signal than a real grocery store (the "food
        swamp" pattern), and `score_gaps` should be free to weight them
        differently rather than have that distinction erased here.

        Straight-line distance is computed later in `score_gaps`; this does
        not yet include transit time — wiring in a GTFS-based routing
        lookup is the next planned upgrade (see the tracker notes), and
        `distance_miles` should be read as an approximation until then.
    """
    south, west, north, east = PILOT_CITY["bbox"]
    return _query_overpass(south, west, north, east)


def _query_overpass(south, west, north, east) -> list:
    query = f"""
    [out:json][timeout:25];
    (
      node["leisure"="garden"]({south},{west},{north},{east});
      way["landuse"="allotments"]({south},{west},{north},{east});
      node["shop"="farm"]({south},{west},{north},{east});
      node["shop"="grocery"]({south},{west},{north},{east});
      node["shop"="supermarket"]({south},{west},{north},{east});
      node["shop"="convenience"]({south},{west},{north},{east});
    );
    out center;
    """
    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=30)
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
    except requests.RequestException as exc:
        print(f"[existing_resources] Overpass query failed, continuing without it: {exc}")
        return []

    out = []
    for el in elements:
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        shop = tags.get("shop")
        if "leisure" in tags or "landuse" in tags:
            kind = "garden"
        elif shop in ("grocery", "supermarket"):
            kind = "grocery"
        elif shop == "convenience":
            kind = "convenience"
        else:
            kind = "farm"
        out.append(
            {
                "kind": kind,
                "name": tags.get("name", "(unnamed)"),
                "lat": lat,
                "lon": lon,
            }
        )
    return out
