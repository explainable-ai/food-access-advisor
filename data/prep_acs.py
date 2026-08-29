"""Enrich prepared Atlas databases with official ACS prioritization fields."""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PILOT_CITY, PILOT_RURAL_COUNTY  # noqa: E402
from data_sources.census_acs import ACSClient, PRIORITIZATION_VARIABLES  # noqa: E402

DATA_DIR = Path(__file__).parent
REGIONS = {"urban": (PILOT_CITY, DATA_DIR / "atlas_pilot_city.db"),
           "rural": (PILOT_RURAL_COUNTY, DATA_DIR / "atlas_rural_county.db")}
FIELDS = ("poverty_universe", "population_below_poverty", "households_total", "households_no_vehicle")


def enrich_database(path, evidence, year):
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
            raise ValueError(
                f"ACS snapshot matched {matched} of {tract_count} Atlas tracts; no changes were committed"
            )
        connection.execute("INSERT OR REPLACE INTO metadata VALUES ('acs_vintage', ?)", (str(year),))
        connection.execute("INSERT OR REPLACE INTO metadata VALUES ('acs_dataset', ?)", (f"{year}/acs/acs5",))
        connection.execute("INSERT OR REPLACE INTO metadata VALUES ('acs_geography_vintage', ?)", (evidence_vintage,))
    return matched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=int(os.getenv("CENSUS_ACS_YEAR", "2024")))
    parser.add_argument("--regions", choices=("all", "urban", "rural"), default="all")
    args = parser.parse_args()
    client = ACSClient(args.year, api_key=os.getenv("CENSUS_API_KEY") or None)
    selected = REGIONS if args.regions == "all" else (args.regions,)
    for region in selected:
        config, path = REGIONS[region]
        evidence = []
        for county in config["county_fips"]:
            evidence.extend(client.fetch_tracts(state_fips=county[:2], county_fips=county[2:],
                                                variables=PRIORITIZATION_VARIABLES))
        print(f"Enriched {enrich_database(path, evidence, args.year)} {region} tracts with ACS {args.year}")


if __name__ == "__main__":
    main()
