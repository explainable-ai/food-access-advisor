"""One-time data-prep script: turn a USDA Food Access Research Atlas download
into the small local SQLite file the Advisor's tools query.

As of 2025, the Atlas's tract-level data ships as two named products on the
same download page (the old standalone "SNAP Retailer Locator" — individual
retailer points — has been discontinued; neither of these two replaces it
with point data, both are tract-level aggregates like the classic Atlas):

  - LRAM (Large Retailer Access Map) — large grocery stores/supermarkets
    only, 2019 data, 2010 tract boundaries. This is the methodological
    continuation of the original Atlas (the page literally says "LRAM was
    formerly known as the Food Access Research Atlas"), so it's the
    RECOMMENDED default here — it isolates genuine full-service grocery
    access rather than counting a dollar store as "access."
  - SRAM (SNAP-authorized Retailer Access Map) — every SNAP-authorized
    store including convenience and dollar stores, 2025 data, 2020 tract
    boundaries. More current, but more lenient — worth downloading as a
    second run for comparison, not as your only severity signal.

Run this once, after you've downloaded one (or both) yourself:
  1. Go to https://www.ers.usda.gov/data-products/food-access-research-atlas/download-the-data
     and download LRAM (recommended first) or SRAM, as a CSV.
  2. Save it as data/raw/food_access_atlas.xlsx (rename regardless of which
     one you picked — this script doesn't care, it matches columns by
     pattern, not by which product you chose).
  3. From the project root, run: python data/prep_atlas.py

Column names have shifted across the Atlas's several re-releases (e.g.
LILATracts_1And10 vs LILATracts_1And10Gen), so this script matches by
pattern instead of one hardcoded spelling. Read the printed column list on
first run — if auto-matching fails, adjust the *_MATCH patterns below to
fit your download and re-run.
"""

import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PILOT_CITY  # noqa: E402

RAW_PATH = Path(__file__).parent / "raw" / "food_access_atlas.xlsx"
DB_PATH = Path(__file__).parent / "atlas_pilot_city.db"

TRACT_COL_MATCH = re.compile(r"censustract", re.I)
POP_COL_MATCH = re.compile(r"^pop2010$|^pop$|^population$", re.I)
LILA_HALF_MATCH = re.compile(r"lilatracts.*half", re.I)
LILA_ONE_MATCH = re.compile(r"lilatracts.*1and10", re.I)
LAT_COL_MATCH = re.compile(r"^lat|latitude", re.I)
LON_COL_MATCH = re.compile(r"^lon|longitude", re.I)


def _find_col(columns, pattern):
    for c in columns:
        if pattern.search(str(c)):
            return c
    return None


def main():
    if not RAW_PATH.exists():
        raise SystemExit(
            f"Missing {RAW_PATH}.\n"
            "Download the Atlas from "
            "https://www.ers.usda.gov/data-products/food-access-research-atlas "
            "and save it there first."
        )

    df = pd.read_excel(RAW_PATH, sheet_name=0)
    print(f"Loaded {len(df)} rows. Columns found:\n{list(df.columns)}\n")

    tract_col = _find_col(df.columns, TRACT_COL_MATCH)
    pop_col = _find_col(df.columns, POP_COL_MATCH)
    half_col = _find_col(df.columns, LILA_HALF_MATCH)
    one_col = _find_col(df.columns, LILA_ONE_MATCH)
    lat_col = _find_col(df.columns, LAT_COL_MATCH)
    lon_col = _find_col(df.columns, LON_COL_MATCH)

    required = {"tract": tract_col, "population": pop_col,
                "low_access_half_mile": half_col, "low_access_one_mile": one_col}
    missing = [name for name, col in required.items() if col is None]
    if missing:
        raise SystemExit(
            f"Couldn't auto-match columns for: {missing}.\n"
            "Open the printed column list above, find the right column "
            "name(s), and adjust the matching *_MATCH pattern near the top "
            "of this file, then re-run."
        )

    df["_tract_fips_str"] = df[tract_col].astype(str)
    county_fips = tuple(PILOT_CITY["county_fips"])
    df = df[df["_tract_fips_str"].str.startswith(county_fips)]
    print(f"{len(df)} tracts in {PILOT_CITY['name']} after filtering to "
          f"county FIPS {county_fips}.")

    if not len(df):
        raise SystemExit(
            "No rows matched — double check config.PILOT_CITY['county_fips'] "
            "against the tract FIPS codes actually in the download."
        )

    conn = sqlite3.connect(DB_PATH)
    conn.execute("DROP TABLE IF EXISTS tracts")
    conn.execute(
        """
        CREATE TABLE tracts (
            tract_fips TEXT PRIMARY KEY,
            population INTEGER,
            low_access_half_mile INTEGER,
            low_access_one_mile INTEGER,
            centroid_lat REAL,
            centroid_lon REAL
        )
        """
    )
    rows = []
    for _, r in df.iterrows():
        rows.append(
            (
                r["_tract_fips_str"],
                int(r[pop_col]) if pd.notna(r[pop_col]) else 0,
                int(bool(r[half_col])) if pd.notna(r[half_col]) else 0,
                int(bool(r[one_col])) if pd.notna(r[one_col]) else 0,
                float(r[lat_col]) if lat_col and pd.notna(r[lat_col]) else None,
                float(r[lon_col]) if lon_col and pd.notna(r[lon_col]) else None,
            )
        )
    conn.executemany("INSERT INTO tracts VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    print(f"Wrote {len(rows)} tracts to {DB_PATH}")

    if lat_col is None or lon_col is None:
        print(
            "\nNOTE: no latitude/longitude column matched. The Atlas "
            "download doesn't always include tract centroids directly — "
            "you may need to join in Census TIGER/Line tract centroids "
            "separately (see README > Data setup) before centroid_lat/lon "
            "are usable for distance scoring."
        )


if __name__ == "__main__":
    main()
