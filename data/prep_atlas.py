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
# NOTE: deliberately NOT `^lat` / `^lon` — the Atlas's own "Low Access
# Tracts" flag columns (e.g. LATracts_half, LATracts1) start with the
# letters "LAT" by coincidence (LA = Low Access), which made an earlier,
# looser version of this pattern false-match one of them as if it were a
# latitude column, silently writing a 0/1 flag into centroid_lat. Anchored
# tightly to actual coordinate-column spellings instead, including the
# Census Gazetteer's INTPTLAT/INTPTLONG (see _load_centroids below) —
# found by running against real column names, not assumed.
LAT_COL_MATCH = re.compile(r"^lat(itude)?$|intptlat", re.I)
LON_COL_MATCH = re.compile(r"^lon(gitude)?$|intptlon", re.I)

CENTROID_PATH = Path(__file__).parent / "raw" / "tract_centroids.txt"


def _find_col(columns, pattern):
    for c in columns:
        if pattern.search(str(c)):
            return c
    return None


def _load_data_sheet(path):
    """The Atlas workbook ships with multiple sheets — a "Notes"/read-me
    sheet (often first, and often the ONLY one openpyxl reports if the
    others are hidden or the notes sheet is simply sheet index 0), a
    variable-lookup sheet, and the actual tract-level data sheet. Sheet
    order and naming have both shifted across re-releases, so rather than
    hardcode sheet_name=0 (which grabbed the notes sheet on this run — 10
    rows, one column, clearly not tract data) this reads every sheet's
    header and picks the one that actually has a census-tract column.
    """
    xl = pd.ExcelFile(path)
    print(f"Sheets in workbook: {xl.sheet_names}\n")

    for name in xl.sheet_names:
        df = pd.read_excel(path, sheet_name=name)
        if _find_col(df.columns, TRACT_COL_MATCH) is not None:
            print(f"Using sheet '{name}' ({len(df)} rows) — has a census-tract column.\n")
            return df

    raise SystemExit(
        "None of the workbook's sheets have a recognizable census-tract "
        f"column. Sheets found: {xl.sheet_names}. Open the file yourself, "
        "find the sheet with tract-level rows, and either rename it to "
        "match here or pass sheet_name= explicitly in _load_data_sheet()."
    )


def _load_centroids() -> dict:
    """Tract centroid lookup (FIPS -> (lat, lon)) from the Census Bureau's
    Gazetteer file, keyed by GEOID (the same 11-digit tract FIPS code the
    Atlas calls CensusTract).

    Optional and separate from the Atlas download on purpose: the LRAM/SRAM
    files don't reliably ship their own lat/lon columns (confirmed against
    a real download — every *_COL_MATCH pattern for coordinates came back
    empty), and unlike the severity flags, a centroid is a pure geometry
    fact that doesn't change release to release, so it's a one-time,
    separate download rather than something to keep re-deriving.

    Get it from:
      https://www2.census.gov/geo/docs/maps-data/data/gazetteer/Gaz_tracts_national.zip
    Unzip it and save the .txt file inside as data/raw/tract_centroids.txt.

    Returns an empty dict (not an error) if the file isn't there — centroid
    lookup degrades to "not available" rather than blocking the rest of
    prep, same as the rest of this script's missing-column handling.
    """
    if not CENTROID_PATH.exists():
        return {}

    # Census Gazetteer files are tab-delimited and (depending on release)
    # sometimes pad column names with trailing whitespace — strip defensively.
    gaz = pd.read_csv(CENTROID_PATH, sep="\t", dtype=str)
    gaz.columns = [c.strip() for c in gaz.columns]

    geoid_col = _find_col(gaz.columns, re.compile(r"^geoid$", re.I))
    lat_col = _find_col(gaz.columns, LAT_COL_MATCH)
    lon_col = _find_col(gaz.columns, LON_COL_MATCH)
    if geoid_col is None or lat_col is None or lon_col is None:
        print(
            f"WARNING: {CENTROID_PATH} doesn't look like a Gazetteer tracts "
            f"file (columns found: {list(gaz.columns)}) — skipping centroid join."
        )
        return {}

    centroids = {}
    for _, row in gaz.iterrows():
        fips = str(row[geoid_col]).strip()
        try:
            centroids[fips] = (float(row[lat_col]), float(row[lon_col]))
        except (TypeError, ValueError):
            continue
    print(f"Loaded {len(centroids)} tract centroids from {CENTROID_PATH.name}.\n")
    return centroids


def main():
    if not RAW_PATH.exists():
        raise SystemExit(
            f"Missing {RAW_PATH}.\n"
            "Download the Atlas from "
            "https://www.ers.usda.gov/data-products/food-access-research-atlas "
            "and save it there first."
        )

    df = _load_data_sheet(RAW_PATH)
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

    centroids = _load_centroids()
    if not centroids and (lat_col is None or lon_col is None):
        print(
            "No centroid source available (neither the Atlas download nor "
            f"{CENTROID_PATH.name} has lat/lon) — every row will get "
            "NULL centroid_lat/centroid_lon. See this file's _load_centroids "
            "docstring for where to get the Gazetteer file. Distance-based "
            "scoring in tools/gap_scorer.py degrades gracefully for tracts "
            "with no centroid (treated as no resource found nearby) rather "
            "than crashing, but it can't rank what it can't place."
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
    centroid_hits = 0
    for _, r in df.iterrows():
        fips = r["_tract_fips_str"]
        if lat_col and lon_col and pd.notna(r[lat_col]) and pd.notna(r[lon_col]):
            centroid_lat, centroid_lon = float(r[lat_col]), float(r[lon_col])
        elif fips in centroids:
            centroid_lat, centroid_lon = centroids[fips]
        else:
            centroid_lat, centroid_lon = None, None
        if centroid_lat is not None:
            centroid_hits += 1

        rows.append(
            (
                fips,
                int(r[pop_col]) if pd.notna(r[pop_col]) else 0,
                int(bool(r[half_col])) if pd.notna(r[half_col]) else 0,
                int(bool(r[one_col])) if pd.notna(r[one_col]) else 0,
                centroid_lat,
                centroid_lon,
            )
        )
    conn.executemany("INSERT INTO tracts VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    print(f"Wrote {len(rows)} tracts to {DB_PATH} ({centroid_hits}/{len(rows)} with a usable centroid)")

    if centroid_hits < len(rows):
        print(
            f"\nNOTE: {len(rows) - centroid_hits} tract(s) have no centroid "
            "(neither the Atlas download nor the Gazetteer join provided "
            "one) — distance-based scoring for those specific tracts will "
            "fall back to 'no resource found nearby' rather than crash, "
            "but they can't be accurately ranked against tracts that do "
            "have a real centroid."
        )


if __name__ == "__main__":
    main()
