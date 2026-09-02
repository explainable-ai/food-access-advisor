"""Prepare and validate Census tract geometry for both approved study areas."""

import hashlib
import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import requests
import shapefile
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PILOT_CITY, PILOT_RURAL_COUNTY  # noqa: E402

TIGER_YEAR = 2023
TRACT_GEOGRAPHY_VINTAGE = "2020"
STATE_FIPS = "17"
TIGER_URL = (
    f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/TRACT/"
    f"tl_{TIGER_YEAR}_{STATE_FIPS}_tract.zip"
)
REQUEST_HEADERS = {"User-Agent": "food-access-advisor/1.0 (data prep script)"}

DATA_DIR = Path(__file__).parent
RAW_DIR = DATA_DIR / "raw"
URBAN_DATABASE = DATA_DIR / "atlas_pilot_city.db"
RURAL_DATABASE = DATA_DIR / "atlas_rural_county.db"
URBAN_CONTEXT_OUTPUT = DATA_DIR / "tract_boundaries_pilot_city.geojson"
RURAL_CONTEXT_OUTPUT = DATA_DIR / "tract_boundaries_rural_county.geojson"
RURAL_SCORING_OUTPUT = DATA_DIR / "tract_boundaries_rural_fringe.geojson"
SIMPLIFY_TOLERANCE_DEGREES = 0.0001
EXPECTED_RURAL_CONTEXT_COUNT = 1733


def _digest(geoids):
    return hashlib.sha256("\n".join(sorted(geoids)).encode()).hexdigest()


def _download_and_extract_shapefile() -> Path:
    """Return the cached statewide TIGER/Line tract shapefile."""
    shp_dir = RAW_DIR / f"tl_{TIGER_YEAR}_{STATE_FIPS}_tract"
    shp_path = shp_dir / f"tl_{TIGER_YEAR}_{STATE_FIPS}_tract.shp"
    if shp_path.exists():
        print(f"Using already-downloaded shapefile at {shp_path}")
        return shp_path

    print(f"Downloading {TIGER_URL} ...")
    response = requests.get(TIGER_URL, headers=REQUEST_HEADERS, timeout=60)
    response.raise_for_status()
    shp_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(shp_dir)
    if not shp_path.exists():
        raise SystemExit(f"TIGER/Line archive did not contain {shp_path.name}")
    return shp_path


def _tract_features_by_counties(shp_path: Path, county_fips_values) -> dict:
    prefixes = tuple(county_fips_values)
    features = {}
    with shapefile.Reader(str(shp_path)) as reader:
        field_names = [field[0] for field in reader.fields[1:]]
        geoid_index = field_names.index("GEOID")
        for shape_record in reader.iterShapeRecords():
            geoid = str(shape_record.record[geoid_index])
            if not geoid.startswith(prefixes):
                continue
            geometry = shape(shape_record.shape.__geo_interface__).simplify(
                SIMPLIFY_TOLERANCE_DEGREES
            )
            features[geoid] = {
                "type": "Feature",
                "properties": {"tract_fips": geoid},
                "geometry": geometry.__geo_interface__,
            }
    return features


def _database_manifest(path: Path) -> tuple[set[str], dict]:
    if not path.exists():
        raise ValueError(f"Prepare the tract database first: {path}")
    with sqlite3.connect(path) as connection:
        has_metadata = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'metadata'"
        ).fetchone()
        if not has_metadata:
            raise ValueError(f"Prepared database has no metadata table: {path}")
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        geoids = {
            str(row[0])
            for row in connection.execute("SELECT tract_fips FROM tracts")
        }
    if metadata.get("geography_vintage") != TRACT_GEOGRAPHY_VINTAGE:
        raise ValueError(
            f"{path} does not use {TRACT_GEOGRAPHY_VINTAGE} tract geography"
        )
    if metadata.get("tract_count") != str(len(geoids)):
        raise ValueError(f"{path} tract count does not match its metadata")
    if metadata.get("tract_fips_sha256") != _digest(geoids):
        raise ValueError(f"{path} tract GEOID set does not match its metadata")
    return geoids, metadata


def _require_manifest(label, geoids, expected_count, expected_digest):
    if len(geoids) != expected_count:
        raise ValueError(f"{label}: expected {expected_count} tracts, found {len(geoids)}")
    if _digest(geoids) != expected_digest:
        raise ValueError(f"{label}: tract GEOID set does not match its approved manifest")


def _require_boundaries(label, geoids, features):
    missing = sorted(geoids - features.keys())
    if missing:
        raise ValueError(
            f"{label}: {len(missing)} prepared tracts lack TIGER boundaries, "
            f"including {missing[0]}"
        )


def _write_geojson(features_by_fips: dict, out_path: Path) -> None:
    feature_collection = {
        "type": "FeatureCollection",
        "features": list(features_by_fips.values()),
    }
    out_path.write_text(json.dumps(feature_collection), encoding="utf-8")
    print(f"Wrote {len(features_by_fips)} tract boundaries to {out_path}")


def main():
    shp_path = _download_and_extract_shapefile()
    rural_context = _tract_features_by_counties(
        shp_path, PILOT_RURAL_COUNTY["county_fips"]
    )
    if len(rural_context) != EXPECTED_RURAL_CONTEXT_COUNT:
        raise ValueError(
            "Chicagoland context geometry: expected "
            f"{EXPECTED_RURAL_CONTEXT_COUNT} tracts, found {len(rural_context)}"
        )

    urban_context = {
        geoid: feature
        for geoid, feature in rural_context.items()
        if geoid.startswith(tuple(PILOT_CITY["county_fips"]))
    }
    _require_manifest(
        "Cook context geometry",
        set(urban_context),
        PILOT_CITY["expected_tract_count"],
        PILOT_CITY["expected_tract_fips_sha256"],
    )

    urban_scoring, _ = _database_manifest(URBAN_DATABASE)
    _require_manifest(
        "Cook scoring database",
        urban_scoring,
        PILOT_CITY["expected_atlas_tract_count"],
        PILOT_CITY["expected_atlas_tract_fips_sha256"],
    )
    _require_boundaries("Cook scoring geometry", urban_scoring, urban_context)
    excluded = set(urban_context) - urban_scoring
    expected_excluded = set(PILOT_CITY["atlas_excluded_tract_fips"])
    if excluded != expected_excluded:
        raise ValueError(
            "Cook context/scoring difference does not match the documented exclusion"
        )

    rural_scoring, _ = _database_manifest(RURAL_DATABASE)
    _require_manifest(
        "Rural scoring database",
        rural_scoring,
        PILOT_RURAL_COUNTY["expected_atlas_tract_count"],
        PILOT_RURAL_COUNTY["expected_atlas_tract_fips_sha256"],
    )
    _require_boundaries("Rural scoring geometry", rural_scoring, rural_context)
    rural_scoring_features = {
        geoid: rural_context[geoid] for geoid in sorted(rural_scoring)
    }

    _write_geojson(urban_context, URBAN_CONTEXT_OUTPUT)
    _write_geojson(rural_context, RURAL_CONTEXT_OUTPUT)
    _write_geojson(rural_scoring_features, RURAL_SCORING_OUTPUT)

    print(
        f"Cook scoring tracts matched: {len(urban_scoring)} of "
        f"{PILOT_CITY['expected_atlas_tract_count']}"
    )
    print(
        "Documented unscored Cook polygon: "
        + ", ".join(sorted(expected_excluded))
    )
    print(
        f"Rural scoring tracts matched: {len(rural_scoring)} of "
        f"{PILOT_RURAL_COUNTY['expected_atlas_tract_count']}"
    )


if __name__ == "__main__":
    main()
