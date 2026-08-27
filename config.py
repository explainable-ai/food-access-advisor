"""Pilot-region configuration.

Swap this to point the Advisor at a different city — nothing else in the
project hardcodes a location. That's the scalability story: the USDA Atlas
already covers every census tract in the country, so adding a city is a
config change plus a data-prep run, not a redesign.
"""

PILOT_CITY = {
    "name": "Chicago, IL",
    # Census county FIPS codes covered by this pilot — Cook County, IL.
    # Find yours at https://www.census.gov/library/reference/code-lists/ansi.html
    "county_fips": ["17031"],
    # Rough bounding box (south, west, north, east) in decimal degrees,
    # used for live Overpass queries. Doesn't need to be precise — it's a
    # search area, not a boundary.
    "bbox": (41.60, -87.85, 42.05, -87.52),
}

TOP_N_DEFAULT = 3
