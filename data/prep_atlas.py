"""Build urban and rural SQLite databases from official USDA Atlas files."""
import argparse
from contextlib import closing
import hashlib
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PILOT_CITY, PILOT_RURAL_COUNTY  # noqa: E402

DATA_DIR = Path(__file__).parent
DEFAULT_INPUT = DATA_DIR / "raw" / "food_access_atlas.xlsx"
DEFAULT_2010_CENTROID_PATH = DATA_DIR / "raw" / "tract_centroids.txt"
DEFAULT_2020_CENTROID_PATH = (
    DATA_DIR
    / "raw"
    / "census_2020_tract_centroids"
    / "2020_Gaz_tracts_national.txt"
)
TRACT_COL_MATCH = re.compile(r"censustract", re.I)
POP_COL_MATCH = re.compile(r"^pop2010$|^pop2020$|^pop$|^population$", re.I)
LAT_COL_MATCH = re.compile(r"^lat(itude)?$|intptlat", re.I)
LON_COL_MATCH = re.compile(r"^lon(gitude)?$|intptlon", re.I)
URBAN_COL_MATCH = re.compile(r"^urban$", re.I)

REGIONS = {
    "urban": {
        "config": PILOT_CITY,
        "db_path": DATA_DIR / "atlas_pilot_city.db",
        "tight": re.compile(r"lilatracts.*half", re.I),
        "wide": re.compile(r"lilatracts.*1and10", re.I),
        "thresholds": "0.5/1 mile",
    },
    "rural": {
        "config": PILOT_RURAL_COUNTY,
        "db_path": DATA_DIR / "atlas_rural_county.db",
        "tight": re.compile(r"lilatracts.*1and10", re.I),
        "wide": re.compile(r"lilatracts.*1and20", re.I),
        "thresholds": "10/20 miles",
    },
}
ATLAS_GEOGRAPHY_VINTAGE = {"LRAM": "2010", "SRAM": "2020"}
SRAM_FILES = {
    "general": "SRAM General Tract Characteristics Data.csv",
    "driving": "SRAM Driving Distance Data.csv",
    "straight": "SRAM Straight Line Distance Data.csv",
}


def _find_col(columns, pattern):
    return next((column for column in columns if pattern.search(str(column).strip())), None)


def _normalize_tract_fips(value):
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    digits = re.sub(r"\D", "", text)
    return digits.zfill(11) if digits else ""


def _read_csv(path):
    """Read official ERS CSV exports, which are distributed as Windows-1252."""
    return pd.read_csv(path, dtype=str, encoding="cp1252")


def _load_legacy_file(path):
    if path.suffix.lower() == ".csv":
        return _read_csv(path)
    workbook = pd.ExcelFile(path)
    for sheet in workbook.sheet_names:
        frame = pd.read_excel(path, sheet_name=sheet, dtype=str)
        if _find_col(frame.columns, TRACT_COL_MATCH):
            return frame
    raise ValueError("No worksheet contains a recognizable CensusTract column")


def _prepare_sram_side(frame, label):
    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    tract = _find_col(frame.columns, re.compile(r"^CensusTract20$", re.I))
    if not tract:
        raise ValueError(f"{label}: missing CensusTract20")
    frame["_sram_tract"] = frame[tract].map(_normalize_tract_fips)
    if (frame["_sram_tract"] == "").any():
        raise ValueError(f"{label}: contains a blank or invalid CensusTract20")
    duplicates = frame.loc[frame["_sram_tract"].duplicated(), "_sram_tract"].unique()
    if len(duplicates):
        raise ValueError(
            f"{label}: contains duplicate CensusTract20 values, including {duplicates[0]}"
        )
    return frame


def _load_sram_bundle(path, distance_method="driving"):
    """Join the current split SRAM release on authoritative 2020 tract GEOID."""
    if distance_method not in ("driving", "straight"):
        raise ValueError("distance_method must be driving or straight")
    general_path = path / SRAM_FILES["general"]
    access_path = path / SRAM_FILES[distance_method]
    missing = [str(item) for item in (general_path, access_path) if not item.exists()]
    if missing:
        raise ValueError("SRAM bundle is missing required file(s): " + ", ".join(missing))

    general = _prepare_sram_side(_read_csv(general_path), "SRAM general file")
    access = _prepare_sram_side(
        _read_csv(access_path), f"SRAM {distance_method}-distance file"
    )
    general_ids = set(general["_sram_tract"])
    access_ids = set(access["_sram_tract"])
    if general_ids != access_ids:
        raise ValueError(
            "SRAM tract sets do not match: "
            f"{len(general_ids - access_ids)} missing from {distance_method} file; "
            f"{len(access_ids - general_ids)} missing from general file"
        )

    access_columns = [
        column
        for column in access.columns
        if column != "_sram_tract" and "lilatracts" in str(column).lower()
    ]
    if not access_columns:
        raise ValueError(
            f"SRAM {distance_method}-distance file has no LILATracts fields"
        )
    merged = general.merge(
        access[["_sram_tract", *access_columns]],
        on="_sram_tract",
        how="inner",
        validate="one_to_one",
    )
    return (
        merged.drop(columns=["_sram_tract"]),
        f"sram_{distance_method}_distance",
        (general_path, access_path),
    )


def _load_data(path, product, distance_method="driving"):
    if path.is_dir():
        if product != "SRAM":
            raise ValueError("Directory input is supported only for the split SRAM release")
        return _load_sram_bundle(path, distance_method)
    return _load_legacy_file(path), "combined_file", (path,)


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


def _load_centroids(path):
    if not path.exists():
        return {}
    frame = pd.read_csv(path, sep="\t", dtype=str)
    frame.columns = [str(column).strip() for column in frame.columns]
    geoid = _find_col(frame.columns, re.compile(r"^geoid$", re.I))
    lat = _find_col(frame.columns, LAT_COL_MATCH)
    lon = _find_col(frame.columns, LON_COL_MATCH)
    if not all((geoid, lat, lon)):
        raise ValueError(f"Centroid file lacks GEOID/INTPTLAT/INTPTLONG: {path}")
    result = {}
    for _, row in frame.iterrows():
        fips = _normalize_tract_fips(row[geoid])
        if not fips:
            continue
        if fips in result:
            raise ValueError(f"Centroid file contains duplicate GEOID {fips}")
        try:
            result[fips] = (float(row[lat]), float(row[lon]))
        except (TypeError, ValueError):
            continue
    return result


def prepare_region_database(
    frame,
    region_name,
    product,
    source_path,
    centroids=None,
    *,
    access_method="combined_file",
    source_files=None,
    require_coordinates=False,
):
    """Filter one configured region and atomically replace its database."""
    region = REGIONS[region_name]
    tract = _find_col(frame.columns, TRACT_COL_MATCH)
    population = _find_col(frame.columns, POP_COL_MATCH)
    tight = _find_col(frame.columns, region["tight"])
    wide = _find_col(frame.columns, region["wide"])
    rural_indicator = (
        _find_col(frame.columns, URBAN_COL_MATCH)
        if region["config"].get("rural_only")
        else None
    )
    required = {
        "tract": tract,
        "population": population,
        "tight threshold": tight,
        "wide threshold": wide,
    }
    if region["config"].get("rural_only"):
        required["USDA urban/rural indicator"] = rural_indicator
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(f"{region_name}: could not match {', '.join(missing)}")

    lat = _find_col(frame.columns, LAT_COL_MATCH)
    lon = _find_col(frame.columns, LON_COL_MATCH)
    data = frame.copy()
    data["_fips"] = data[tract].map(_normalize_tract_fips)
    prefixes = tuple(region["config"]["county_fips"])
    data = data[data["_fips"].str.startswith(prefixes)]
    if rural_indicator:
        expected_value = float(region["config"].get("rural_indicator_value", 0))
        data = data[
            data[rural_indicator].map(
                lambda value: _numeric_or_default(value, default=None)
                == expected_value
            )
        ]
    if data.empty:
        raise ValueError(f"{region_name}: no tracts matched county FIPS {prefixes}")

    centroids = centroids or {}
    rows = []
    for _, row in data.iterrows():
        fips = row["_fips"]
        try:
            has_coordinates = (
                lat and lon and pd.notna(row[lat]) and pd.notna(row[lon])
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

    expected_count = region["config"].get("expected_tract_count")
    expected_vintage = region["config"].get("tract_geography_vintage")
    if (
        expected_count
        and ATLAS_GEOGRAPHY_VINTAGE[product] == expected_vintage
        and len(rows) != expected_count
    ):
        raise ValueError(
            f"{region_name}: expected {expected_count} {expected_vintage} tracts, "
            f"found {len(rows)}"
        )
    expected_digest = region["config"].get("expected_tract_fips_sha256")
    actual_digest = hashlib.sha256(
        "\n".join(sorted(row[0] for row in rows)).encode()
    ).hexdigest()
    if (
        expected_digest
        and ATLAS_GEOGRAPHY_VINTAGE[product] == expected_vintage
        and actual_digest != expected_digest
    ):
        raise ValueError(
            f"{region_name}: tract GEOID set does not match the authoritative "
            f"{expected_vintage} manifest"
        )
    if require_coordinates:
        missing_coordinates = [row[0] for row in rows if row[4] is None or row[5] is None]
        if missing_coordinates:
            raise ValueError(
                f"{region_name}: {len(missing_coordinates)} tracts lack {expected_vintage} "
                f"centroids, including {missing_coordinates[0]}"
            )

    target = region["db_path"]
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    os.close(fd)
    source_files = tuple(source_files or (source_path,))
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE tracts (tract_fips TEXT PRIMARY KEY, "
                    "population INTEGER, low_access_half_mile INTEGER, "
                    "low_access_one_mile INTEGER, centroid_lat REAL, centroid_lon REAL)"
                )
                connection.executemany(
                    "INSERT INTO tracts VALUES (?, ?, ?, ?, ?, ?)", rows
                )
                connection.execute(
                    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.executemany(
                    "INSERT INTO metadata VALUES (?, ?)",
                    {
                        "data_mode": "real",
                        "product": product,
                        "region": region_name,
                        "region_name": region["config"]["name"],
                        "thresholds": region["thresholds"],
                        "county_fips": ",".join(region["config"]["county_fips"]),
                        "rural_only": str(
                            bool(region["config"].get("rural_only"))
                        ).lower(),
                        "rural_indicator": (
                            f"{rural_indicator}="
                            f"{region['config'].get('rural_indicator_value', 0)}"
                            if rural_indicator
                            else "not_applied"
                        ),
                        "geography_vintage": ATLAS_GEOGRAPHY_VINTAGE[product],
                        "access_method": access_method,
                        "source_file": str(source_path),
                        "source_files": "|".join(str(item) for item in source_files),
                        "tract_count": str(len(rows)),
                        "tract_fips_sha256": actual_digest,
                        "prepared_at": datetime.now(timezone.utc).isoformat(),
                    }.items(),
                )
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
    parser.add_argument(
        "--distance-method",
        choices=("driving", "straight"),
        default="driving",
        help="SRAM access measure; driving is the production default",
    )
    parser.add_argument(
        "--centroids",
        type=Path,
        help="Census tract Gazetteer file matching the Atlas geography vintage",
    )
    args = parser.parse_args()
    if not args.input.exists():
        raise SystemExit(f"Missing Atlas input: {args.input}")

    centroid_path = args.centroids or (
        DEFAULT_2020_CENTROID_PATH
        if args.product == "SRAM"
        else DEFAULT_2010_CENTROID_PATH
    )
    if not centroid_path.exists():
        raise SystemExit(f"Missing matching centroid file: {centroid_path}")

    frame, access_method, source_files = _load_data(
        args.input, args.product, args.distance_method
    )
    centroids = _load_centroids(centroid_path)
    selected = REGIONS if args.regions == "all" else (args.regions,)
    for region in selected:
        count, path = prepare_region_database(
            frame,
            region,
            args.product,
            args.input,
            centroids,
            access_method=access_method,
            source_files=source_files,
            require_coordinates=True,
        )
        print(f"Wrote {count} {region} tracts to {path}")


if __name__ == "__main__":
    main()
