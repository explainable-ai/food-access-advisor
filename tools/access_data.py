"""Access-data tool: the "stock" side of the canvas.

Reads a small local SQLite database built by `data/prep_atlas.py` from the
USDA Food Access Research Atlas. Ships with a handful of clearly-labeled
sample rows so `python agent.py` runs before you've done the real data-prep
step — swap in the full download when you're ready (see README > Data setup).
"""

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import boto3
from strands import tool

from config import PILOT_CITY, PILOT_RURAL_COUNTY

DB_PATH = Path(__file__).parent.parent / "data" / "atlas_pilot_city.db"
DEFAULT_DATABASE_KEY = "prepared-data/atlas_pilot_city.db"
DEFAULT_CACHE_PATH = Path("/tmp/food-access-advisor/atlas_pilot_city.db")
URBAN_CONTEXT_PATH = Path(__file__).parent.parent / "data" / "urban_scoring_context.json"
URBAN_CONTEXT_FORMAT = "food-access-advisor-urban-context-v1"

# Separate file, not a second table in the same DB: the urban and rural
# databases come from different Atlas download runs (see
# config.PILOT_RURAL_COUNTY), so a missing rural file should never
# accidentally fall through to reading Chicago's tracts.
RURAL_DB_PATH = Path(__file__).parent.parent / "data" / "atlas_rural_county.db"
DEFAULT_RURAL_DATABASE_KEY = "prepared-data/atlas_rural_fringe.db"
DEFAULT_RURAL_CACHE_PATH = Path("/tmp/food-access-advisor/atlas_rural_fringe.db")

CORE_COLUMNS = (
    "tract_fips",
    "population",
    "low_access_half_mile",
    "low_access_one_mile",
    "centroid_lat",
    "centroid_lon",
)
ACS_COLUMNS = (
    "poverty_universe",
    "population_below_poverty",
    "households_total",
    "households_no_vehicle",
)
RURAL_CONTINUOUS_COLUMNS = (
    "low_access_population_share",
    "low_income_low_access_share",
    "no_vehicle_low_access_share",
)
HEATMAP_COLUMNS = CORE_COLUMNS + ACS_COLUMNS
HEATMAP_METADATA = (
    "region",
    "region_name",
    "geography_vintage",
    "acs_vintage",
    "acs_dataset",
    "acs_geography_vintage",
    "tract_count",
    "tract_fips_sha256",
    "atlas_excluded_tract_fips",
    "atlas_exclusion_reason",
)


class PreparedTractDataError(RuntimeError):
    """The all-tract heatmap dataset has not been prepared for this deployment."""


def _materialize_database(local_path, key_env, cache_env, default_key, default_cache, label):
    """Return a packaged database or materialize its prepared S3 artifact."""
    if local_path.exists():
        return local_path
    bucket = os.getenv("TRACT_DATA_BUCKET") or os.getenv("EVIDENCE_BUCKET")
    if not bucket:
        return local_path
    key = os.getenv(key_env, default_key).strip("/")
    target = Path(os.getenv(cache_env, default_cache))
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            boto3.client("s3").download_fileobj(bucket, key, temporary)
        temporary_path.replace(target)
    except Exception as error:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        raise PreparedTractDataError(
            f"prepared {label} tract database is unavailable at s3://{bucket}/{key}: {error}"
        ) from error
    return target


def _prepared_database_path():
    return _materialize_database(
        DB_PATH,
        "TRACT_DATA_KEY",
        "TRACT_DATA_CACHE_PATH",
        DEFAULT_DATABASE_KEY,
        DEFAULT_CACHE_PATH,
        "Cook County",
    )


def _prepared_rural_database_path():
    return _materialize_database(
        RURAL_DB_PATH,
        "RURAL_TRACT_DATA_KEY",
        "RURAL_TRACT_DATA_CACHE_PATH",
        DEFAULT_RURAL_DATABASE_KEY,
        DEFAULT_RURAL_CACHE_PATH,
        "Chicagoland rural-fringe",
    )


def _load_urban_context():
    """Read and validate the prepared food-insecurity/transit overlay."""
    if not URBAN_CONTEXT_PATH.exists():
        raise PreparedTractDataError(
            f"prepared Chicago scoring context is unavailable at {URBAN_CONTEXT_PATH}; "
            "run data/prep_urban_context.py before deployment"
        )
    try:
        payload = json.loads(URBAN_CONTEXT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PreparedTractDataError(
            "prepared Chicago scoring context is unreadable or invalid"
        ) from error
    records = payload.get("records")
    problems = []
    if payload.get("context_format") != URBAN_CONTEXT_FORMAT:
        problems.append("unsupported context format")
    if not isinstance(records, list) or not records:
        problems.append("context contains no tract records")
        records = []
    by_fips = {str(record.get("tract_fips")): record for record in records}
    if len(by_fips) != len(records):
        problems.append("context contains duplicate tract GEOIDs")
    if payload.get("tract_count") != len(records):
        problems.append("context tract count does not match its manifest")
    chicago = [record for record in records if record.get("is_chicago")]
    if payload.get("chicago_tract_count") != len(chicago):
        problems.append("Chicago tract count does not match its manifest")
    transit = [record for record in chicago if record.get("transit_burden") is not None]
    if payload.get("transportation_scored_tract_count") != len(transit):
        problems.append("transportation coverage does not match its manifest")
    if len(transit) != len(chicago):
        problems.append("one or more Chicago tracts are missing transportation evidence")
    if any(
        record.get("food_insecurity_rate") is None
        or not 0 <= float(record["food_insecurity_rate"]) <= 1
        for record in records
    ):
        problems.append("one or more tracts have invalid food-insecurity evidence")
    if problems:
        raise PreparedTractDataError(
            "prepared Chicago scoring context is incomplete (" + "; ".join(problems) + ")"
        )
    return by_fips


def _overlay_urban_context(rows, *, require_complete=False):
    context = _load_urban_context()
    row_fips = {str(row["tract_fips"]) for row in rows}
    missing = sorted(row_fips - set(context))
    extra = sorted(set(context) - row_fips) if require_complete else []
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"missing {len(missing)} requested tract GEOIDs")
        if extra:
            detail.append(f"contains {len(extra)} unexpected tract GEOIDs")
        raise PreparedTractDataError(
            "prepared Chicago scoring context does not match the Atlas tract universe ("
            + "; ".join(detail)
            + ")"
        )
    return [{**row, **context[str(row["tract_fips"])]} for row in rows]


def _read_database(
    path,
    limit=None,
    low_access_only=True,
    rural_gap_only=False,
    urban_context=False,
    require_complete_context=False,
):
    connection = None
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        available = {row[1] for row in connection.execute("PRAGMA table_info(tracts)")}
        optional_columns = ACS_COLUMNS + RURAL_CONTINUOUS_COLUMNS
        optional = ", ".join(
            name if name in available else f"NULL AS {name}"
            for name in optional_columns
        )
        if low_access_only and rural_gap_only:
            raise PreparedTractDataError("conflicting tract filters were requested")
        if rural_gap_only:
            if "low_income_low_access_share" not in available:
                raise PreparedTractDataError(
                    "prepared rural tract database is missing continuous SRAM access evidence"
                )
            where_clause = (
                "WHERE population > 0 AND low_income_low_access_share > 0"
            )
        else:
            where_clause = (
                "WHERE low_access_half_mile = 1 OR low_access_one_mile = 1"
                if low_access_only else ""
            )
        limit_clause = "LIMIT ?" if limit is not None else ""
        parameters = (limit,) if limit is not None else ()
        rows = connection.execute(
            f"""SELECT tract_fips, population, low_access_half_mile,
                       low_access_one_mile, centroid_lat, centroid_lon, {optional}
                FROM tracts
                {where_clause}
                ORDER BY population DESC {limit_clause}""",
            parameters,
        ).fetchall()
        prepared = [{**dict(row), "data_mode": "real"} for row in rows]
        if urban_context:
            prepared = _overlay_urban_context(
                prepared,
                require_complete=require_complete_context,
            )
        return prepared
    except sqlite3.Error as error:
        raise PreparedTractDataError(
            f"prepared tract database is unreadable or has an invalid schema at {path}"
        ) from error
    finally:
        if connection:
            connection.close()


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
    path = _prepared_database_path()
    if not path.exists():
        return _sample_tracts()[:limit]

    return _read_database(path, limit, urban_context=True)


def _validate_complete_heatmap_database(path):
    try:
        with sqlite3.connect(path) as connection:
            available = {row[1] for row in connection.execute("PRAGMA table_info(tracts)")}
            missing_columns = sorted(set(HEATMAP_COLUMNS) - available)
            has_metadata = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'metadata'"
            ).fetchone()
            metadata = dict(connection.execute("SELECT key, value FROM metadata")) if has_metadata else {}
            missing_metadata = [name for name in HEATMAP_METADATA if not metadata.get(name)]
            tract_count = connection.execute("SELECT COUNT(*) FROM tracts").fetchone()[0]
            tract_rows = (
                connection.execute(
                    """SELECT tract_fips, population, low_access_half_mile,
                              low_access_one_mile, centroid_lat, centroid_lon
                       FROM tracts"""
                ).fetchall()
                if not (set(CORE_COLUMNS) - available)
                else []
            )
    except sqlite3.Error as error:
        raise PreparedTractDataError(
            "prepared Cook County tract database is unreadable or has an invalid schema; "
            "run data/prep_atlas.py and data/prep_acs.py before deployment"
        ) from error
    problems = []
    if missing_columns:
        problems.append(f"missing required columns: {', '.join(missing_columns)}")
    if missing_metadata:
        problems.append(f"missing preparation metadata: {', '.join(missing_metadata)}")
    if tract_count == 0:
        problems.append("tract table is empty")
    expected_count = int(
        PILOT_CITY.get("expected_atlas_tract_count", PILOT_CITY["expected_tract_count"])
    )
    if tract_count != expected_count:
        problems.append(
            f"expected {expected_count} SRAM-covered Cook County tracts, found {tract_count}"
        )
    if metadata.get("region") != "urban" or metadata.get("region_name") != PILOT_CITY["name"]:
        problems.append("database metadata does not identify the Cook County urban study area")
    expected_vintage = str(PILOT_CITY["tract_geography_vintage"])
    if metadata.get("geography_vintage") != expected_vintage:
        problems.append(f"tract geography is not the required {expected_vintage} vintage")
    if metadata.get("acs_geography_vintage") != expected_vintage:
        problems.append(f"ACS geography is not the required {expected_vintage} vintage")
    actual_fips = [str(row[0]) for row in tract_rows]
    prefixes = tuple(PILOT_CITY["county_fips"])
    if actual_fips and any(len(fips) != 11 or not fips.isdigit() or not fips.startswith(prefixes)
                           for fips in actual_fips):
        problems.append("tract table contains a GEOID outside the configured Cook County FIPS")
    actual_digest = hashlib.sha256("\n".join(sorted(actual_fips)).encode()).hexdigest()
    expected_atlas_digest = PILOT_CITY.get(
        "expected_atlas_tract_fips_sha256", PILOT_CITY["expected_tract_fips_sha256"]
    )
    if actual_digest != expected_atlas_digest:
        problems.append("tract GEOID set does not match the authoritative SRAM manifest")
    if metadata.get("tract_fips_sha256") != actual_digest:
        problems.append("tract GEOID set does not match the prepared evidence manifest")
    expected_excluded = ",".join(PILOT_CITY.get("atlas_excluded_tract_fips", []))
    if metadata.get("atlas_excluded_tract_fips") != expected_excluded:
        problems.append("Atlas exclusion metadata does not match the configured tract universe")
    if metadata.get("atlas_exclusion_reason") != PILOT_CITY.get(
        "atlas_exclusion_reason", "not_applicable"
    ):
        problems.append("Atlas exclusion reason is missing or unexpected")
    try:
        metadata_count = int(metadata.get("tract_count", ""))
    except ValueError:
        metadata_count = -1
    if metadata_count != tract_count:
        problems.append("tract row count does not match the prepared evidence manifest")
    if tract_rows and any(
        row[1] is None or row[4] is None or row[5] is None for row in tract_rows
    ):
        problems.append("one or more tracts are missing population or centroid values")
    if tract_rows and any(row[2] not in (0, 1) or row[3] not in (0, 1) for row in tract_rows):
        problems.append("one or more tracts have null or non-binary low-access flags")
    if problems:
        raise PreparedTractDataError(
            "prepared Cook County tract database is incomplete (" + "; ".join(problems) + "); "
            "run data/prep_atlas.py and data/prep_acs.py before deployment"
        )


def get_all_tracts() -> list:
    """Return every prepared Cook County tract for the evidence heatmap.

    Unlike the Advisor candidate tool, this read does not filter to low-access
    tracts and does not return sample rows. A complete county surface must
    never be fabricated when the prepared Atlas/ACS database is missing.
    """
    path = _prepared_database_path()
    if not path.exists():
        raise PreparedTractDataError(
            f"prepared Cook County tract database is unavailable at {path}; "
            "run data/prep_atlas.py and data/prep_acs.py, then package it or upload it to "
            f"s3://$EVIDENCE_BUCKET/{DEFAULT_DATABASE_KEY} before deployment"
        )
    _validate_complete_heatmap_database(path)
    return _read_database(
        path,
        low_access_only=False,
        urban_context=True,
        require_complete_context=True,
    )


@tool
def get_low_access_rural_tracts(limit: int = 25) -> list:
    """Return prepared low-income, low-access rural-fringe tracts.

    The database contains only USDA-classified rural tracts from the seven
    configured Chicagoland counties. A missing prepared artifact is an
    unavailable-data condition, never permission to substitute Alexander
    County or illustrative rows.
    """
    path = _prepared_rural_database_path()
    if not path.exists():
        raise PreparedTractDataError(
            f"prepared Chicagoland rural-fringe database is unavailable at {path}; "
            "run data/prep_atlas.py for the rural region, then upload it to "
            f"s3://$EVIDENCE_BUCKET/{DEFAULT_RURAL_DATABASE_KEY}"
        )
    _validate_complete_rural_database(path)
    return _read_database(
        path,
        limit,
        low_access_only=False,
        rural_gap_only=True,
    )


def _validate_complete_rural_database(path):
    try:
        with sqlite3.connect(path) as connection:
            available = {row[1] for row in connection.execute("PRAGMA table_info(tracts)")}
            required = set(CORE_COLUMNS + ACS_COLUMNS + RURAL_CONTINUOUS_COLUMNS)
            missing_columns = sorted(required - available)
            has_metadata = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'metadata'"
            ).fetchone()
            metadata = dict(connection.execute("SELECT key, value FROM metadata")) if has_metadata else {}
            geoids = [row[0] for row in connection.execute("SELECT tract_fips FROM tracts")]
    except sqlite3.Error as error:
        raise PreparedTractDataError(
            "prepared rural tract database is unreadable or has an invalid schema"
        ) from error
    problems = []
    expected_count = int(PILOT_RURAL_COUNTY["expected_atlas_tract_count"])
    if missing_columns:
        problems.append(f"missing required columns: {', '.join(missing_columns)}")
    if len(geoids) != expected_count:
        problems.append(f"expected {expected_count} rural tracts, found {len(geoids)}")
    actual_digest = hashlib.sha256("\n".join(sorted(geoids)).encode()).hexdigest()
    if actual_digest != PILOT_RURAL_COUNTY["expected_atlas_tract_fips_sha256"]:
        problems.append("rural tract GEOID set does not match the authoritative SRAM manifest")
    if metadata.get("tract_fips_sha256") != actual_digest:
        problems.append("rural database metadata does not match its tract GEOID set")
    if metadata.get("rural_food_access_metric") != "low_income_low_access_share_10mi":
        problems.append("rural database does not identify the approved continuous access metric")
    if problems:
        raise PreparedTractDataError(
            "prepared rural tract database is incomplete (" + "; ".join(problems) + ")"
        )


def get_all_rural_tracts() -> list:
    """Return all 72 prepared rural-fringe tracts for geometry and scoring."""
    path = _prepared_rural_database_path()
    if not path.exists():
        raise PreparedTractDataError(f"prepared rural tract database is unavailable at {path}")
    _validate_complete_rural_database(path)
    return _read_database(path, low_access_only=False)


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
