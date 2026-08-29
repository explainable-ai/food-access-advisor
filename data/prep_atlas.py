"""Build urban and rural SQLite databases from an official USDA Atlas file."""
import argparse
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PILOT_CITY, PILOT_RURAL_COUNTY  # noqa: E402

DATA_DIR = Path(__file__).parent
DEFAULT_INPUT = DATA_DIR / "raw" / "food_access_atlas.xlsx"
CENTROID_PATH = DATA_DIR / "raw" / "tract_centroids.txt"
TRACT_COL_MATCH = re.compile(r"censustract", re.I)
POP_COL_MATCH = re.compile(r"^pop2010$|^pop2020$|^pop$|^population$", re.I)
LAT_COL_MATCH = re.compile(r"^lat(itude)?$|intptlat", re.I)
LON_COL_MATCH = re.compile(r"^lon(gitude)?$|intptlon", re.I)

REGIONS = {
    "urban": {"config": PILOT_CITY, "db_path": DATA_DIR / "atlas_pilot_city.db",
              "tight": re.compile(r"lilatracts.*half", re.I),
              "wide": re.compile(r"lilatracts.*1and10", re.I), "thresholds": "0.5/1 mile"},
    "rural": {"config": PILOT_RURAL_COUNTY, "db_path": DATA_DIR / "atlas_rural_county.db",
              "tight": re.compile(r"lilatracts.*1and10", re.I),
              "wide": re.compile(r"lilatracts.*1and20", re.I), "thresholds": "10/20 miles"},
}
ATLAS_GEOGRAPHY_VINTAGE = {"LRAM": "2010", "SRAM": "2020"}


def _find_col(columns, pattern):
    return next((column for column in columns if pattern.search(str(column))), None)


def _load_data(path):
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=str)
    workbook = pd.ExcelFile(path)
    for sheet in workbook.sheet_names:
        frame = pd.read_excel(path, sheet_name=sheet, dtype=str)
        if _find_col(frame.columns, TRACT_COL_MATCH):
            return frame
    raise ValueError("No worksheet contains a recognizable CensusTract column")


def _normalize_tract_fips(value):
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    digits = re.sub(r"\D", "", text)
    return digits.zfill(11) if digits else ""


def _numeric_or_default(value, default=0.0):
    """Convert a populated Atlas cell; treat pandas NA/NaN as missing."""
    if pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _binary_flag(value):
    return int(_numeric_or_default(value) != 0)


def _load_centroids(path=CENTROID_PATH):
    if not path.exists():
        return {}
    frame = pd.read_csv(path, sep="\t", dtype=str)
    frame.columns = [str(column).strip() for column in frame.columns]
    geoid = _find_col(frame.columns, re.compile(r"^geoid$", re.I))
    lat, lon = _find_col(frame.columns, LAT_COL_MATCH), _find_col(frame.columns, LON_COL_MATCH)
    if not all((geoid, lat, lon)):
        return {}
    result = {}
    for _, row in frame.iterrows():
        try:
            result[_normalize_tract_fips(row[geoid])] = (float(row[lat]), float(row[lon]))
        except (TypeError, ValueError):
            continue
    return result


def prepare_region_database(
    frame,
    region_name,
    product,
    source_path,
    centroids=None,
):
    """Filter one configured region and atomically replace its database."""
    region = REGIONS[region_name]

    tract = _find_col(frame.columns, TRACT_COL_MATCH)
    population = _find_col(frame.columns, POP_COL_MATCH)
    tight = _find_col(frame.columns, region["tight"])
    wide = _find_col(frame.columns, region["wide"])

    missing = [
        name
        for name, value in {
            "tract": tract,
            "population": population,
            "tight threshold": tight,
            "wide threshold": wide,
        }.items()
        if value is None
    ]

    if missing:
        raise ValueError(
            f"{region_name}: could not match {', '.join(missing)}"
        )

    lat = _find_col(frame.columns, LAT_COL_MATCH)
    lon = _find_col(frame.columns, LON_COL_MATCH)

    data = frame.copy()
    data["_fips"] = data[tract].map(_normalize_tract_fips)

    prefixes = tuple(region["config"]["county_fips"])
    data = data[data["_fips"].str.startswith(prefixes)]

    if data.empty:
        raise ValueError(
            f"{region_name}: no tracts matched county FIPS {prefixes}"
        )

    centroids = centroids or {}
    rows = []

    for _, row in data.iterrows():
        fips = row["_fips"]

        try:
            has_coordinates = (
                lat
                and lon
                and pd.notna(row[lat])
                and pd.notna(row[lon])
            )
            coordinates = (
                (float(row[lat]), float(row[lon]))
                if has_coordinates
                else centroids.get(fips, (None, None))
            )
        except (TypeError, ValueError):
            coordinates = centroids.get(fips, (None, None))

        rows.append(
            (
                fips,
                int(_numeric_or_default(row[population])),
                _binary_flag(row[tight]),
                _binary_flag(row[wide]),
                *coordinates,
            )
        )

    target = region["db_path"]
    fd, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.",
        dir=target.parent,
    )
    os.close(fd)

    try:
        # `closing` explicitly releases the SQLite file handle before
        # Windows attempts to replace the destination database.
        with closing(sqlite3.connect(temporary)) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE tracts ("
                    "tract_fips TEXT PRIMARY KEY, "
                    "population INTEGER, "
                    "low_access_half_mile INTEGER, "
                    "low_access_one_mile INTEGER, "
                    "centroid_lat REAL, "
                    "centroid_lon REAL)"
                )

                connection.executemany(
                    "INSERT INTO tracts VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )

                connection.execute(
                    "CREATE TABLE metadata ("
                    "key TEXT PRIMARY KEY, "
                    "value TEXT NOT NULL)"
                )

                connection.executemany(
                    "INSERT INTO metadata VALUES (?, ?)",
                    {
                        "data_mode": "real",
                        "product": product,
                        "region": region_name,
                        "region_name": region["config"]["name"],
                        "thresholds": region["thresholds"],
                        "geography_vintage": ATLAS_GEOGRAPHY_VINTAGE[
                            product
                        ],
                        "source_file": str(source_path),
                        "prepared_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }.items(),
                )

        # The SQLite connection is closed before this Windows file operation.
        os.replace(temporary, target)

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    return len(rows), target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--product", choices=("LRAM", "SRAM"), required=True)
    parser.add_argument("--regions", choices=("all", "urban", "rural"), default="all")
    args = parser.parse_args()
    if not args.input.exists():
        raise SystemExit(f"Missing Atlas input: {args.input}")
    frame, centroids = _load_data(args.input), _load_centroids()
    selected = REGIONS if args.regions == "all" else (args.regions,)
    for region in selected:
        count, path = prepare_region_database(frame, region, args.product, args.input, centroids)
        print(f"Wrote {count} {region} tracts to {path}")


if __name__ == "__main__":
    main()
