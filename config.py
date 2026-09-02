"""Pilot-region configuration.

The urban study area is Chicago/Cook County. The route study area is the
approved Chicagoland rural fringe: rural census tracts in Cook, Kane,
Kendall, Grundy, Will, Kankakee, and McHenry Counties. Region changes must
be followed by a data-preparation run; the configuration is not a runtime
location selector.
"""

PILOT_CITY = {
    "name": "Chicago, IL",
    # Census county FIPS codes covered by this pilot — Cook County, IL.
    "county_fips": ["17031"],
    # 2020 Census geography contains 1,332 Cook County tracts, including
    # the water tract that the frontend deliberately leaves unshaded.
    "tract_geography_vintage": "2020",
    "expected_tract_count": 1332,
    # SHA-256 of the sorted, newline-delimited authoritative 2020 Cook
    # County tract GEOIDs used by the tract-boundary preparation pipeline.
    "expected_tract_fips_sha256": "e8c642ba760be2ee7a5712fb27961a9bb7a1e7f2a6317f80599f4089aebb376e",
    # The 2025 SRAM evidence universe has 1,331 Cook tracts. Census water
    # tract 17031990000 has a map polygon but no SRAM evidence row, so it
    # remains unshaded and must never be interpreted as a zero-need tract.
    "expected_atlas_tract_count": 1331,
    "expected_atlas_tract_fips_sha256": "aac4ceecdc0e4ea16437ad9a6ff592b863376a52a6b48ee3e07f97ebf874ed0f",
    "atlas_excluded_tract_fips": ["17031990000"],
    "atlas_exclusion_reason": "not_present_in_usda_sram_2025",
    # Cook County-wide resource coverage box (south, west, north, east),
    # with a small buffer so nearby cross-boundary stores count.
    "bbox": (41.45, -88.30, 42.20, -87.50),
}

# Route study area. Only rows whose USDA Atlas Urban field is 0 are
# prepared for this region; county membership alone is not treated as a
# rural classification.
PILOT_RURAL_COUNTY = {
    "name": "Chicagoland Rural Fringe, IL",
    "county_fips": [
        "17031",  # Cook
        "17089",  # Kane
        "17093",  # Kendall
        "17063",  # Grundy
        "17197",  # Will
        "17091",  # Kankakee
        "17111",  # McHenry
    ],
    "rural_only": True,
    "rural_indicator_column": "Urban",
    "rural_indicator_value": 0,
    # Overall map/data extent. Resource refreshes use the three smaller
    # planning bands below to avoid one oversized Overpass request.
    "bbox": (40.80, -89.05, 42.55, -87.45),
    "resource_areas": [
        {
            "name": "western_and_southwestern",
            "counties": ["Kane", "Kendall", "Grundy"],
            "bbox": (41.00, -89.05, 42.20, -88.00),
        },
        {
            "name": "southern",
            "counties": ["Cook", "Will", "Kankakee"],
            "bbox": (40.80, -88.35, 41.75, -87.45),
        },
        {
            "name": "northern_and_northwestern",
            "counties": ["McHenry"],
            "bbox": (42.10, -88.75, 42.55, -88.00),
        },
    ],
    # USDA LRAM combined thresholds mean 1 mile for urban tracts and
    # 10/20 miles for rural tracts. Because rural rows are explicitly
    # filtered above, these represent the 10- and 20-mile rural measures.
    "low_access_col_10mi": "LILATracts_1And10",
    "low_access_col_20mi": "LILATracts_1And20",
}

TOP_N_DEFAULT = 3
