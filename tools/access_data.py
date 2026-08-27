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
        return [dict(r) for r in rows]
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
        },
        {
            "tract_fips": "17031680000",
            "population": 2870,
            "low_access_half_mile": 1,
            "low_access_one_mile": 1,
            "centroid_lat": 41.7699,
            "centroid_lon": -87.6553,
        },
        {
            "tract_fips": "17031710000",
            "population": 4025,
            "low_access_half_mile": 0,
            "low_access_one_mile": 1,
            "centroid_lat": 41.7524,
            "centroid_lon": -87.6091,
        },
    ]
