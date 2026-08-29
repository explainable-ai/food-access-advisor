"""Access-data tool: the "stock" side of the canvas.

Reads a small local SQLite database built by `data/prep_atlas.py` from the
USDA Food Access Research Atlas. Ships with a handful of clearly-labeled
sample rows so `python agent.py` runs before you've done the real data-prep
step — swap in the full download when you're ready (see README > Data setup).
"""

import sqlite3
from pathlib import Path

from strands import tool

from config import PILOT_CITY

DB_PATH = Path(__file__).parent.parent / "data" / "atlas_pilot_city.db"

# Separate file, not a second table in the same DB: the urban and rural
# databases come from different Atlas download runs (see
# config.PILOT_RURAL_COUNTY), so a missing rural file should never
# accidentally fall through to reading Chicago's tracts.
RURAL_DB_PATH = Path(__file__).parent.parent / "data" / "atlas_rural_county.db"


@tool
def get_low_access_tracts(limit: int = 25) -> list:
    """Return low-income, low-access census tracts for the pilot city.

    There is deliberately no city/region argument here. The underlying
    database is permanently scoped to whatever region `data/prep_atlas.py`
    was run against (config.PILOT_CITY) — that's a boundary enforced by
    what data physically exists on disk, not by trusting the model to only
    ask for the right place. An earlier version of this tool accepted a
    `city` parameter that was never actually used to filter anything; it's
    removed rather than fixed-in-place, because a parameter the model can
    set but that does nothing is worse than no parameter at all — it invites
    the model (or a future contributor) to believe region-switching works
    through this tool when it doesn't. To point the Advisor at a different
    city, change config.py and re-run data/prep_atlas.py — a deploy-time
    decision, not a run-time one.

    Args:
        limit: Maximum number of tracts to return, ordered by population
            descending. This is a coarse pre-filter, not the final
            ranking — that happens in `score_gaps`.

    Returns:
        A list of dicts, each with `tract_fips`, `population`,
        `low_access_half_mile`, `low_access_one_mile`, `centroid_lat`,
        `centroid_lon`. Sourced from the USDA Food Access Research Atlas —
        cite the Atlas's publication year in any answer built from this.
    """
    if not DB_PATH.exists():
        return _sample_tracts()[:limit]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT tract_fips, population, low_access_half_mile,
                   low_access_one_mile, centroid_lat, centroid_lon
            FROM tracts
            WHERE low_access_half_mile = 1 OR low_access_one_mile = 1
            ORDER BY population DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [{**dict(r), "data_mode": "real"} for r in rows]
    finally:
        conn.close()


@tool
def get_low_access_rural_tracts(limit: int = 25) -> list:
    """Return low-income, low-access census tracts for the rural pilot
    county (see config.PILOT_RURAL_COUNTY — currently Alexander County, IL).

    Same boundary discipline as `get_low_access_tracts`: no region
    argument. This is permanently scoped by which database file it reads
    (RURAL_DB_PATH, a separate file from the urban tracts DB) — not a
    filter a model could bypass.

    Reuses the same `low_access_half_mile` / `low_access_one_mile` field
    names as the urban tool, even though the rural Atlas columns behind
    them (`LILATracts_1And10` / `LILATracts_1And20`, see
    config.PILOT_RURAL_COUNTY) mean a 10-mile and 20-mile threshold, not a
    half-mile and one-mile one. That's deliberate, not a copy-paste
    mistake: `score_gaps` only ever reads these two fields as "low access
    at the tighter threshold" vs. "low access at the wider one" to weigh
    severity — it never reads the literal mileage the field name implies —
    so reusing the same two field names lets the one deterministic scorer
    rank both urban and rural tracts without a rural-specific copy of it.

    `data/prep_atlas.py --product LRAM --regions rural` builds this database.
    Until that prep step runs, the tool returns clearly labelled sample rows.

    Args:
        limit: Maximum number of tracts to return, ordered by population
            descending.

    Returns:
        A list of dicts, same shape as `get_low_access_tracts`'s output.
    """
    if not RURAL_DB_PATH.exists():
        return _sample_rural_tracts()[:limit]

    conn = sqlite3.connect(RURAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT tract_fips, population, low_access_half_mile,
                   low_access_one_mile, centroid_lat, centroid_lon
            FROM tracts
            WHERE low_access_half_mile = 1 OR low_access_one_mile = 1
            ORDER BY population DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [{**dict(r), "data_mode": "real"} for r in rows]
    finally:
        conn.close()


def _sample_tracts() -> list:
    """Illustrative placeholder rows — NOT real Atlas data, and the FIPS
    codes and coordinates below are not verified against the real dataset.
    Replace by running data/prep_atlas.py against the actual USDA download
    (see README) before using this for anything but a UI smoke test."""
    return [
        {
            "tract_fips": "17031840000",
            "population": 3210,
            "low_access_half_mile": 1,
            "low_access_one_mile": 1,
            "centroid_lat": 41.7942,
            "centroid_lon": -87.6328,
            "data_mode": "sample",
        },
        {
            "tract_fips": "17031680000",
            "population": 2870,
            "low_access_half_mile": 1,
            "low_access_one_mile": 1,
            "centroid_lat": 41.7699,
            "centroid_lon": -87.6553,
            "data_mode": "sample",
        },
        {
            "tract_fips": "17031710000",
            "population": 4025,
            "low_access_half_mile": 0,
            "low_access_one_mile": 1,
            "centroid_lat": 41.7524,
            "centroid_lon": -87.6091,
            "data_mode": "sample",
        },
    ]


def _sample_rural_tracts() -> list:
    """Illustrative placeholder rows for Alexander County, IL (seat: Cairo)
    — NOT real Atlas data. FIPS codes follow Alexander County's real state+
    county prefix (17003) but the tract suffixes and coordinates are
    illustrative, not verified against a real download — same caveat as
    `_sample_tracts()`. Run `data/prep_atlas.py --product LRAM --regions rural`
    with a real Atlas download before using this for
    anything but a smoke test."""
    return [
        {
            "tract_fips": "17003960100",
            "population": 1560,
            "low_access_half_mile": 1,
            "low_access_one_mile": 1,
            "centroid_lat": 37.0059,
            "centroid_lon": -89.1770,
            "data_mode": "sample",
        },
        {
            "tract_fips": "17003960200",
            "population": 980,
            "low_access_half_mile": 1,
            "low_access_one_mile": 1,
            "centroid_lat": 37.0512,
            "centroid_lon": -89.2185,
            "data_mode": "sample",
        },
        {
            "tract_fips": "17003960300",
            "population": 1215,
            "low_access_half_mile": 0,
            "low_access_one_mile": 1,
            "centroid_lat": 37.1203,
            "centroid_lon": -89.2564,
            "data_mode": "sample",
        },
    ]
