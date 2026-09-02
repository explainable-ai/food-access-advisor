"""Collect an immutable, API-key-free ACS snapshot for configured counties."""

import argparse
import json
import os
import sys
import tempfile
from dataclasses import asdict
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import PILOT_CITY, PILOT_RURAL_COUNTY  # noqa: E402
from data_sources.census_acs import ACSClient, PRIORITIZATION_VARIABLES, variable_fields  # noqa: E402


def configured_counties():
    return sorted(set(PILOT_CITY["county_fips"]) | set(PILOT_RURAL_COUNTY["county_fips"]))


def collect_snapshot(client, county_fips_codes, variables=PRIORITIZATION_VARIABLES):
    variables = tuple(variables)
    counties = []
    for full_fips in sorted(set(county_fips_codes)):
        if len(full_fips) != 5 or not full_fips.isdigit():
            raise ValueError(f"County FIPS must contain five digits: {full_fips!r}")
        payload, retrieved_at = client.fetch_payload(
            state_fips=full_fips[:2],
            county_fips=full_fips[2:],
            variables=variables,
        )
        counties.append(
            {
                "county_fips": full_fips,
                "retrieved_at": retrieved_at.astimezone(timezone.utc).isoformat(),
                "row_count": len(payload) - 1,
                "payload": payload,
            }
        )
    return {
        "snapshot_format": "food-access-advisor-acs-v1",
        "authority": "U.S. Census Bureau",
        "dataset": f"{client.year}/acs/acs5",
        "dataset_url": client.dataset_url,
        "year": client.year,
        "geography": "2020 census tract",
        "county_fips": [item["county_fips"] for item in counties],
        "fields": variable_fields(variables),
        "variables": [asdict(variable) for variable in variables],
        "api_key_persisted": False,
        "source_row_count": sum(item["row_count"] for item in counties),
        "counties": counties,
    }


def write_snapshot(path, snapshot):
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    client = ACSClient(args.year, api_key=os.getenv("CENSUS_API_KEY") or None)
    snapshot = collect_snapshot(client, configured_counties())
    write_snapshot(args.output, snapshot)
    print(f"Wrote {snapshot['source_row_count']} ACS tract rows to {args.output}")


if __name__ == "__main__":
    main()
