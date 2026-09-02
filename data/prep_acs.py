"""Enrich prepared Atlas databases from an immutable ACS snapshot."""
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PILOT_CITY, PILOT_RURAL_COUNTY  # noqa: E402
from data_sources.census_acs import ACSClient, PRIORITIZATION_VARIABLES, variable_fields  # noqa: E402

DATA_DIR = Path(__file__).parent
REGIONS = {"urban": (PILOT_CITY, DATA_DIR / "atlas_pilot_city.db"),
           "rural": (PILOT_RURAL_COUNTY, DATA_DIR / "atlas_rural_county.db")}
FIELDS = ("poverty_universe", "population_below_poverty", "households_total", "households_no_vehicle")
SNAPSHOT_FORMAT = "food-access-advisor-acs-v1"


def _tract_digest(geoids):
    return hashlib.sha256("\n".join(sorted(geoids)).encode()).hexdigest()


def _file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_snapshot(path, year):
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if snapshot.get("snapshot_format") != SNAPSHOT_FORMAT:
        raise ValueError("Unsupported ACS snapshot format")
    if snapshot.get("year") != year or snapshot.get("dataset") != f"{year}/acs/acs5":
        raise ValueError("ACS snapshot year or dataset does not match the requested release")
    if snapshot.get("geography") != "2020 census tract":
        raise ValueError("ACS snapshot does not use the required 2020 tract geography")
    if snapshot.get("api_key_persisted") is not False:
        raise ValueError("ACS snapshot must explicitly confirm that no API key was persisted")
    expected_fields = variable_fields(PRIORITIZATION_VARIABLES)
    if snapshot.get("fields") != expected_fields:
        raise ValueError("ACS snapshot field manifest does not match required prioritization fields")
    counties = snapshot.get("counties")
    if not isinstance(counties, list) or not counties:
        raise ValueError("ACS snapshot contains no county payloads")
    by_county = {}
    row_count = 0
    client = ACSClient(year)
    for item in counties:
        fips = str(item.get("county_fips", ""))
        if len(fips) != 5 or not fips.isdigit() or fips in by_county:
            raise ValueError(f"ACS snapshot has an invalid or duplicate county FIPS: {fips!r}")
        payload = item.get("payload")
        client.validate_payload(payload)
        header = payload[0]
        required_header = {*expected_fields, "state", "county", "tract"}
        if not required_header.issubset(header):
            raise ValueError(f"ACS snapshot payload is missing required fields for county {fips}")
        actual_rows = len(payload) - 1
        if item.get("row_count") != actual_rows:
            raise ValueError(f"ACS snapshot row count mismatch for county {fips}")
        retrieved_at = datetime.fromisoformat(item["retrieved_at"])
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError(f"ACS snapshot retrieved_at is not timezone-aware for {fips}")
        state_index, county_index = header.index("state"), header.index("county")
        wrong_geography = any(
            str(row[state_index]) + str(row[county_index]) != fips
            for row in payload[1:]
        )
        if wrong_geography:
            raise ValueError(f"ACS snapshot payload geography does not match county {fips}")
        by_county[fips] = (payload, retrieved_at)
        row_count += actual_rows
    if snapshot.get("source_row_count") != row_count:
        raise ValueError("ACS snapshot total row count does not match county payloads")
    return snapshot, by_county, _file_sha256(path)


def snapshot_evidence(client, by_county, county_fips_codes):
    evidence = []
    missing = sorted(set(county_fips_codes) - set(by_county))
    if missing:
        raise ValueError("ACS snapshot is missing configured counties: " + ", ".join(missing))
    for full_fips in county_fips_codes:
        payload, retrieved_at = by_county[full_fips]
        evidence.extend(client.parse_payload(
            payload,
            retrieved_at=retrieved_at,
            variables=PRIORITIZATION_VARIABLES,
        ))
    geoids = [item.tract_geoid for item in evidence]
    if len(geoids) != len(set(geoids)):
        raise ValueError("ACS snapshot contains duplicate tract GEOIDs")
    return evidence


def enrich_database(
    path,
    evidence,
    year,
    allow_extra_evidence=False,
    allowed_extra_geoids=(),
    *,
    snapshot_file=None,
    snapshot_sha256=None,
):
    if not path.exists():
        raise FileNotFoundError(f"Prepare the Atlas database first: {path}")
    if not evidence:
        raise ValueError("ACS response contained no tract evidence")
    by_geoid = {item.tract_geoid: item for item in evidence}
    evidence_vintages = {item.geography_vintage for item in evidence}
    if len(evidence_vintages) != 1:
        raise ValueError(f"ACS evidence contains mixed geography vintages: {sorted(evidence_vintages)}")
    evidence_vintage = next(iter(evidence_vintages))
    with sqlite3.connect(path) as connection:
        has_metadata = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'metadata'"
        ).fetchone()
        if not has_metadata:
            raise ValueError("Atlas database has no geography metadata; rebuild it with data/prep_atlas.py")
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        atlas_vintage = metadata.get("geography_vintage")
        if not atlas_vintage:
            raise ValueError("Atlas database has no geography_vintage; rebuild it before ACS enrichment")
        if atlas_vintage != evidence_vintage:
            raise ValueError(
                f"Tract geography mismatch: Atlas uses {atlas_vintage}, ACS uses {evidence_vintage}. "
                "Use a matching tract vintage or an explicit Census crosswalk."
            )
        atlas_geoids = {row[0] for row in connection.execute("SELECT tract_fips FROM tracts")}
        evidence_geoids = set(by_geoid)
        absent_from_acs = atlas_geoids - evidence_geoids
        extra_acs = evidence_geoids - atlas_geoids
        unexpected_extra_acs = extra_acs - set(allowed_extra_geoids)
        if absent_from_acs or (unexpected_extra_acs and not allow_extra_evidence):
            raise ValueError(
                "ACS and Atlas tract sets do not match: "
                f"{len(absent_from_acs)} Atlas tracts are absent from ACS and "
                f"{len(unexpected_extra_acs)} unexpected ACS tracts are outside the "
                "prepared Atlas set; no changes were committed"
            )
        if extra_acs:
            by_geoid = {geoid: item for geoid, item in by_geoid.items() if geoid in atlas_geoids}
        for field in FIELDS:
            if field not in {row[1] for row in connection.execute("PRAGMA table_info(tracts)")}:
                connection.execute(f"ALTER TABLE tracts ADD COLUMN {field} REAL")
        connection.execute(f"UPDATE tracts SET {', '.join(f'{field} = NULL' for field in FIELDS)}")
        matched = 0
        for geoid, item in by_geoid.items():
            values = [item.values[field].value if field in item.values else None for field in FIELDS]
            cursor = connection.execute(
                f"UPDATE tracts SET {', '.join(f'{field} = ?' for field in FIELDS)} WHERE tract_fips = ?",
                (*values, geoid),
            )
            matched += cursor.rowcount
        tract_count = connection.execute("SELECT COUNT(*) FROM tracts").fetchone()[0]
        if matched != tract_count:
            raise ValueError(f"ACS snapshot matched {matched} of {tract_count} Atlas tracts; no changes were committed")
        values = {
            "acs_vintage": str(year),
            "acs_dataset": f"{year}/acs/acs5",
            "acs_geography_vintage": evidence_vintage,
            "acs_snapshot_file": str(snapshot_file or ""),
            "acs_snapshot_sha256": str(snapshot_sha256 or ""),
            "tract_count": str(tract_count),
            "tract_fips_sha256": _tract_digest(atlas_geoids),
        }
        connection.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", values.items())
    return matched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--regions", choices=("all", "urban", "rural"), default="all")
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    _, by_county, snapshot_sha256 = load_snapshot(args.snapshot, args.year)
    client = ACSClient(args.year)
    selected = REGIONS if args.regions == "all" else (args.regions,)
    for region in selected:
        config, path = REGIONS[region]
        evidence = snapshot_evidence(client, by_county, config["county_fips"])
        matched = enrich_database(
            path,
            evidence,
            args.year,
            allow_extra_evidence=bool(config.get("rural_only")),
            allowed_extra_geoids=config.get("atlas_excluded_tract_fips", ()),
            snapshot_file=args.snapshot,
            snapshot_sha256=snapshot_sha256,
        )
        print(f"Enriched {matched} {region} tracts with snapshotted ACS {args.year}")


if __name__ == "__main__":
    main()
