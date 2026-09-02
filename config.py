"""Pilot-region configuration.

Swap PILOT_CITY to point the Advisor at a different city, or PILOT_RURAL_COUNTY
to point the (planned) Route Advisor at a different rural county — nothing
else in the project hardcodes a location. That's the scalability story: the
USDA Atlas already covers every census tract in the country, so adding a
region is a config change plus a data-prep run, not a redesign.
"""

PILOT_CITY = {
    "name": "Chicago, IL",
    # Census county FIPS codes covered by this pilot — Cook County, IL.
    # Find yours at https://www.census.gov/library/reference/code-lists/ansi.html
    "county_fips": ["17031"],
    # Cook County-wide resource coverage box (south, west, north, east),
    # with a small buffer so nearby cross-boundary stores count. Prepared
    # resource snapshots used by the heatmap must cover this entire box.
    "bbox": (41.45, -88.30, 42.20, -87.50),
}

# Rural pilot for the Route Advisor (a separate sibling agent from the
# Advisor — see the rural systems-thinking pass in docs/design-canvas.html
# for why "same agent, different mode flag" was rejected). Chosen for a
# real, currently-documented, and genuinely rural food-access gap rather
# than a demo-convenient guess:
#
#   Alexander County, IL (seat: Cairo) is Illinois's poorest county.
#   Population fell from ~8,200 (2010) to 5,240 (2020) — the steepest
#   county population decline in the entire US that decade. Cairo's only
#   grocery store, a community co-op called Rise Community Market, opened
#   June 2023 after seven years with none, and was still struggling badly
#   as of mid-2024 reporting (equipment failures, a failed adjacent café,
#   residents still driving to Walmart out of habit). About 17% of Cairo
#   families have no vehicle; the nearest big chain grocery store is a
#   genuine ~30-mile round trip. That last point is a real-world
#   confirmation of this project's own rural systems-thinking finding: a
#   subsidized store opening doesn't close the loop by itself (see the
#   "capacity-driven Success-to-the-Successful" risk in the design canvas)
#   — sources: STLPR (stlpr.org, Aug 2024), WSIL-TV, Illinois Public Media,
#   ProPublica, ILGA/DCEO's Illinois Grocery Initiative, and Wikipedia
#   (Alexander County, Illinois) for the FIPS code and population figures.
#
# Keeping this in Illinois (same state as PILOT_CITY) means the Route
# Advisor draws on the exact same Atlas download as the Advisor — one
# dataset, one state, two very different food-access realities.
PILOT_RURAL_COUNTY = {
    "name": "Alexander County, IL",
    "county_fips": ["17003"],
    # Deliberately wider margin than a tight county-line box: real rural
    # grocery trips cross county (and state) lines routinely — e.g.
    # Cairo residents driving to a Walmart in Sikeston, MO. A resource just
    # across the line is still a real resource a resident actually uses,
    # so the search area intentionally reaches past the county boundary.
    # (The FIPS filter above is what keeps the *tracts we score* limited
    # to this county — that boundary is enforced in code, same discipline
    # as PILOT_CITY; this bbox only widens what OSM gets searched.)
    "bbox": (36.85, -89.75, 37.45, -88.85),
    # USDA's rural low-access thresholds are wider than the urban ones:
    # 10 miles and 20 miles from the nearest grocery source, vs. the
    # urban half-mile/one-mile flags PILOT_CITY uses. Real column names
    # confirmed against an actual 2019 LRAM download.
    "low_access_col_10mi": "LILATracts_1And10",
    "low_access_col_20mi": "LILATracts_1And20",
}

TOP_N_DEFAULT = 3
