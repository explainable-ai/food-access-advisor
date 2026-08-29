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
    by_geoid = {item.tract_geoid: item for item in evidence}
    with sqlite3.connect(path) as connection:
        for field in FIELDS:
            if field not in {row[1] for row in connection.execute("PRAGMA table_info(tracts)")}:
                connection.execute(f"ALTER TABLE tracts ADD COLUMN {field} REAL")
        matched = 0
        for geoid, item in by_geoid.items():
            values = [item.values[field].value if field in item.values else None for field in FIELDS]
            cursor = connection.execute(
                f"UPDATE tracts SET {', '.join(f'{field} = ?' for field in FIELDS)} WHERE tract_fips = ?",
                (*values, geoid),
            )
            matched += cursor.rowcount
        connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT OR REPLACE INTO metadata VALUES ('acs_vintage', ?)", (str(year),))
        connection.execute("INSERT OR REPLACE INTO metadata VALUES ('acs_dataset', ?)", (f"{year}/acs/acs5",))
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
