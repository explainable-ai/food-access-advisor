"""Prepare household-weighted CNT H+T tract features in Atlas databases."""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PILOT_CITY, PILOT_RURAL_COUNTY  # noqa: E402


DATA_DIR = Path(__file__).parent
DEFAULT_INPUT = DATA_DIR / "raw" / "hta_2022" / "htaindex2022_data_blkgrps_17.csv"
EXPECTED_SOURCE_ROWS = 9896
EXPECTED_CSV_SHA256 = "948f719391851926a14620a0bef087cbaac7fcac5b22dd7702271a6527cedc07"
CANONICAL_ARCHIVE_SHA256 = "c56734dae3ca4a84025d11409255e59323c623102c94f9741a347e0eeb36979e"
H_T_RELEASE = "2022"
H_T_GEOGRAPHY_VINTAGE = "2020"

REGIONS = {
    "urban": (PILOT_CITY, DATA_DIR / "atlas_pilot_city.db"),
    "rural": (PILOT_RURAL_COUNTY, DATA_DIR / "atlas_rural_county.db"),
}

SOURCE_METRICS = {
    "ht_80ami": "housing_transportation_cost_burden_pct",
    "h_80ami": "housing_cost_burden_pct",
    "t_80ami": "transportation_cost_burden_pct",
    "t_cost_80ami": "annual_transportation_cost",
    "autos_per_hh_80ami": "autos_per_household",
    "vmt_per_hh_80ami": "vehicle_miles_traveled_per_household",
}
REQUIRED_COLUMNS = {"blkgrp", "households", *SOURCE_METRICS}


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_block_group(value):
    text = str(value).strip().strip('"')
    if text.endswith(".0"):
        text = text[:-2]
    if len(text) != 12 or not text.isdigit():
        raise ValueError(f"Invalid Census block-group GEOID: {value!r}")
    return text


def load_source(path, *, expected_rows=None, expected_sha256=None):
    """Load and validate the canonical Illinois block-group source."""
    if not path.exists():
        raise FileNotFoundError(f"Missing H+T input: {path}")
    actual_sha256 = _sha256(path)
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise ValueError(
            "H+T CSV checksum does not match the approved canonical source: "
            f"expected {expected_sha256}, found {actual_sha256}"
        )

    frame = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError("H+T source is missing required columns: " + ", ".join(missing))
    if expected_rows is not None and len(frame) != expected_rows:
        raise ValueError(
            f"H+T source row count is {len(frame)}; expected {expected_rows}"
        )

    frame = frame[["blkgrp", "households", *SOURCE_METRICS]].copy()
    frame["block_group_geoid"] = frame["blkgrp"].map(_normalize_block_group)
    duplicates = frame.loc[
        frame["block_group_geoid"].duplicated(), "block_group_geoid"
    ].unique()
    if len(duplicates):
        raise ValueError(
            "H+T source contains duplicate block-group GEOIDs, including "
            f"{duplicates[0]}"
        )
    if not frame["block_group_geoid"].str.startswith("17").all():
        raise ValueError("H+T Illinois source contains a GEOID outside state FIPS 17")

    frame["tract_fips"] = frame["block_group_geoid"].str[:11]
    numeric = ["households", *SOURCE_METRICS]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame["households"].isna().any() or (frame["households"] < 0).any():
        raise ValueError("H+T source contains missing or negative household weights")
    return frame, actual_sha256


def _weighted_average(group, column):
    valid = group[column].notna() & (group["households"] > 0)
    if not valid.any():
        return None
    weights = group.loc[valid, "households"]
    return float((group.loc[valid, column] * weights).sum() / weights.sum())


def aggregate_to_tracts(frame, tract_geoids):
    """Aggregate block-group model outputs using positive household weights."""
    tract_geoids = set(tract_geoids)
    selected = frame[frame["tract_fips"].isin(tract_geoids)].copy()
    present = set(selected["tract_fips"])
    absent = sorted(tract_geoids - present)
    if absent:
        raise ValueError(
            f"H+T source has no block groups for {len(absent)} prepared tracts, "
            f"including {absent[0]}"
        )

    rows = []
    for tract_fips, group in selected.groupby("tract_fips", sort=True):
        values = [_weighted_average(group, source) for source in SOURCE_METRICS]
        valid_transportation = group[
            group["t_80ami"].notna() & (group["households"] > 0)
        ]
        rows.append(
            (
                tract_fips,
                *values,
                int(len(group)),
                int(len(valid_transportation)),
                float(valid_transportation["households"].sum()),
            )
        )
    return rows


def _database_geoids_and_vintage(path):
    if not path.exists():
        raise FileNotFoundError(f"Prepare the Atlas database first: {path}")
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not {"tracts", "metadata"}.issubset(tables):
            raise ValueError(f"Prepared database lacks tracts or metadata: {path}")
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        geoids = [row[0] for row in connection.execute("SELECT tract_fips FROM tracts")]
    vintage = metadata.get("geography_vintage")
    if vintage != H_T_GEOGRAPHY_VINTAGE:
        raise ValueError(
            f"H+T uses {H_T_GEOGRAPHY_VINTAGE} geography but {path} uses "
            f"{vintage or 'an unknown vintage'}; an explicit crosswalk is required"
        )
    return geoids


def write_region_features(path, rows, *, source_path, source_sha256, source_rows):
    """Replace one region's H+T feature table only after all validation passes."""
    prepared_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DROP TABLE IF EXISTS hta_tract_features")
        connection.execute(
            "CREATE TABLE hta_tract_features ("
            "tract_fips TEXT PRIMARY KEY, "
            "housing_transportation_cost_burden_pct REAL, "
            "housing_cost_burden_pct REAL, "
            "transportation_cost_burden_pct REAL, "
            "annual_transportation_cost REAL, "
            "autos_per_household REAL, "
            "vehicle_miles_traveled_per_household REAL, "
            "source_block_groups INTEGER NOT NULL, "
            "valid_transportation_block_groups INTEGER NOT NULL, "
            "households_weight REAL NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO hta_tract_features VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        metadata = {
            "hta_authority": "Center for Neighborhood Technology",
            "hta_release": H_T_RELEASE,
            "hta_geography_vintage": H_T_GEOGRAPHY_VINTAGE,
            "hta_source_geography": "census_block_group",
            "hta_tract_aggregation": "household_weighted_positive_households",
            "hta_scoring_metric": "t_80ami",
            "hta_affordability_benchmark_pct": "15",
            "hta_source_file": str(source_path),
            "hta_source_csv_sha256": source_sha256,
            "hta_source_archive_sha256": CANONICAL_ARCHIVE_SHA256,
            "hta_source_rows": str(source_rows),
            "hta_prepared_at": prepared_at,
        }
        connection.executemany(
            "INSERT OR REPLACE INTO metadata VALUES (?, ?)", metadata.items()
        )


def prepare(input_path, regions, *, expected_rows=None, expected_sha256=None):
    frame, source_sha256 = load_source(
        input_path,
        expected_rows=expected_rows,
        expected_sha256=expected_sha256,
    )
    prepared = []
    # Validate and aggregate every selected region before mutating any database.
    for region in regions:
        _, path = REGIONS[region]
        geoids = _database_geoids_and_vintage(path)
        rows = aggregate_to_tracts(frame, geoids)
        prepared.append((region, path, rows))

    report = {
        "source": str(input_path),
        "source_rows": len(frame),
        "source_csv_sha256": source_sha256,
        "source_archive_sha256": CANONICAL_ARCHIVE_SHA256,
        "release": H_T_RELEASE,
        "geography_vintage": H_T_GEOGRAPHY_VINTAGE,
        "regions": {},
    }
    for region, path, rows in prepared:
        write_region_features(
            path,
            rows,
            source_path=input_path,
            source_sha256=source_sha256,
            source_rows=len(frame),
        )
        missing_metric = sum(row[3] is None for row in rows)
        report["regions"][region] = {
            "database": str(path),
            "tracts": len(rows),
            "missing_transportation_cost_burden": missing_metric,
        }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--regions", choices=("all", "urban", "rural"), default="all")
    parser.add_argument("--quality-report", type=Path)
    args = parser.parse_args()
    selected = tuple(REGIONS) if args.regions == "all" else (args.regions,)
    report = prepare(
        args.input,
        selected,
        expected_rows=EXPECTED_SOURCE_ROWS,
        expected_sha256=EXPECTED_CSV_SHA256,
    )
    rendered = json.dumps(report, indent=2)
    if args.quality_report:
        args.quality_report.parent.mkdir(parents=True, exist_ok=True)
        args.quality_report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
