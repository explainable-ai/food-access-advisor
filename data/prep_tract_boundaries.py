"""One-time data-prep script: fetch real Census tract boundary geometry for
the two pilot regions and save it as GeoJSON the frontend's map can render.

A separate script from prep_atlas.py on purpose, even though both live in
data/ and both prep one-time reference data: prep_atlas.py's job is the
Atlas's socioeconomic/low-access columns (a spreadsheet download, no
geometry), this script's job is boundary geometry from a completely
different Census source (TIGER/Line shapefiles) -- conflating the two into
one file/one main() would mix two unrelated download-and-convert workflows
that can be re-run independently of each other.

Why this exists: every tract row anywhere in this project (the real Atlas
DB, and _sample_tracts()/_sample_rural_tracts() in tools/access_data.py)
carries only a centroid_lat/centroid_lon point -- there is no polygon
anywhere in this codebase. A map that shows "highlighted low-access census
tracts" as filled areas (not just point markers) needs real boundary
polygons, keyed by the same tract_fips every tract row already has.

Uses pyshp + shapely rather than geopandas/fiona -- no GDAL dependency,
which matters given this project's own history of pip friction on Windows
(see the model.py / bedrocksetup.md debugging earlier in this project).

Run once, from the project root: python data/prep_tract_boundaries.py

IMPORTANT — this could not be run end-to-end from the sandbox this was
written in: census.gov is blocked by that sandbox's network egress policy
(confirmed via a direct curl attempt, which got a 403 at the proxy before
ever reaching Census). The download-and-parse logic below is written
against TIGER/Line's documented, stable shapefile format and field names
(GEOID = the 11-digit tract FIPS, matching this project's tract_fips
exactly), and the GeoJSON-conversion/simplification/join-check logic was
verified in that sandbox against a synthetic shapefile built with the same
pyshp APIs -- but the actual Census download itself needs to be run and
confirmed by whoever has real network access to census.gov.
"""

import io
import sys
import zipfile
from pathlib import Path

import requests
import shapefile
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PILOT_CITY, PILOT_RURAL_COUNTY  # noqa: E402
from tools.access_data import get_low_access_rural_tracts, get_low_access_tracts  # noqa: E402

# Both configured regions are in Illinois (state FIPS 17). The rural
# region spans several counties, so county membership is handled as a set.
TIGER_YEAR = 2023
STATE_FIPS = "17"
TIGER_URL = (
    f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/TRACT/"
    f"tl_{TIGER_YEAR}_{STATE_FIPS}_tract.zip"
)
REQUEST_HEADERS = {"User-Agent": "food-access-advisor/1.0 (data prep script)"}

RAW_DIR = Path(__file__).parent / "raw"
SIMPLIFY_TOLERANCE_DEGREES = 0.0001  # roughly ~10m at these latitudes -- keeps shape, cuts file size

REGIONS = [
    (PILOT_CITY, Path(__file__).parent / "tract_boundaries_pilot_city.geojson", get_low_access_tracts),
    (PILOT_RURAL_COUNTY, Path(__file__).parent / "tract_boundaries_rural_county.geojson", get_low_access_rural_tracts),
]


def _download_and_extract_shapefile() -> Path:
    """Download the statewide TIGER/Line tract shapefile once (cached in
    data/raw/ across re-runs) and return the path to the extracted .shp."""
    shp_dir = RAW_DIR / f"tl_{TIGER_YEAR}_{STATE_FIPS}_tract"
    shp_path = shp_dir / f"tl_{TIGER_YEAR}_{STATE_FIPS}_tract.shp"
    if shp_path.exists():
        print(f"Using already-downloaded shapefile at {shp_path}")
        return shp_path

    print(f"Downloading {TIGER_URL} ...")
    response = requests.get(TIGER_URL, headers=REQUEST_HEADERS, timeout=60)
    response.raise_for_status()

    shp_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        zf.extractall(shp_dir)

    if not shp_path.exists():
        raise SystemExit(
            f"Downloaded and extracted the TIGER/Line zip, but didn't find "
            f"the expected {shp_path.name} inside. TIGER/Line's file naming "
            "has been stable for years, but if the Census Bureau changed it, "
            f"look in {shp_dir} for the actual .shp filename and adjust "
            "shp_path above."
        )
    return shp_path


def _tract_features_by_counties(shp_path: Path, county_fips_values) -> dict:
    """Return tract features for every configured county FIPS prefix."""
    prefixes = tuple(county_fips_values)
    features = {}
    with shapefile.Reader(str(shp_path)) as reader:
        geoid_index = [f[0] for f in reader.fields[1:]].index("GEOID")
        for shape_record in reader.iterShapeRecords():
            geoid = shape_record.record[geoid_index]
            if not geoid.startswith(prefixes):
                continue
            geom = shape(shape_record.shape.__geo_interface__).simplify(SIMPLIFY_TOLERANCE_DEGREES)
            features[geoid] = {
                "type": "Feature",
                "properties": {"tract_fips": geoid},
                "geometry": geom.__geo_interface__,
            }
    return features


def _write_geojson(features_by_fips: dict, out_path: Path) -> None:
    import json

    feature_collection = {"type": "FeatureCollection", "features": list(features_by_fips.values())}
    out_path.write_text(json.dumps(feature_collection))
    print(f"Wrote {len(features_by_fips)} tract boundaries to {out_path}")


def main():
    shp_path = _download_and_extract_shapefile()

    for region_config, out_path, get_tracts in REGIONS:
        county_fips_values = region_config["county_fips"]
        print(
            f"\n{region_config['name']} "
            f"(county FIPS {', '.join(county_fips_values)}):"
        )

        features_by_fips = _tract_features_by_counties(shp_path, county_fips_values)
        _write_geojson(features_by_fips, out_path)

        # Explicit join-check: a tract with no matching boundary would
        # otherwise just render as un-highlighted with no error at all.
        known_tracts = {t["tract_fips"] for t in get_tracts()}
        missing = known_tracts - features_by_fips.keys()
        if missing:
            print(
                f"WARNING: {len(missing)} of {len(known_tracts)} known tract(s) "
                f"have no matching boundary in the TIGER/Line download: {sorted(missing)}"
            )
        else:
            print(f"All {len(known_tracts)} known tracts have a matching boundary.")


if __name__ == "__main__":
    main()
