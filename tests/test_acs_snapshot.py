import json
from datetime import datetime, timezone

import pytest

from data.collect_acs import collect_snapshot, write_snapshot
from data.prep_acs import load_snapshot, snapshot_evidence
from data_sources.census_acs import ACSClient, PRIORITIZATION_VARIABLES, variable_fields


HEADER = ["NAME", *variable_fields(PRIORITIZATION_VARIABLES), "state", "county", "tract"]
# variable_fields already starts with NAME; avoid a duplicate in the fixture.
HEADER = [HEADER[0], *HEADER[2:]]
ROW = ["Tract 1", "2500", "120", "42000", "2500", "1000", "80", "180", "35",
       "900", "50", "225", "25", "17", "031", "010100"]


class FakeClient:
    year = 2024
    dataset_url = "https://api.census.gov/data/2024/acs/acs5"

    def fetch_payload(self, *, state_fips, county_fips, variables):
        return [HEADER, ROW], datetime(2026, 9, 2, tzinfo=timezone.utc)


def test_snapshot_is_api_key_free_and_round_trips(tmp_path):
    snapshot = collect_snapshot(FakeClient(), ["17031"])
    assert snapshot["api_key_persisted"] is False
    assert "test-key" not in json.dumps(snapshot)
    path = tmp_path / "acs.json"
    write_snapshot(path, snapshot)

    loaded, counties, digest = load_snapshot(path, 2024)
    evidence = snapshot_evidence(ACSClient(2024), counties, ["17031"])
    assert loaded["source_row_count"] == 1
    assert len(digest) == 64
    assert evidence[0].tract_geoid == "17031010100"
    assert evidence[0].values["population_below_poverty"].value == 225


def test_snapshot_rejects_wrong_release(tmp_path):
    snapshot = collect_snapshot(FakeClient(), ["17031"])
    path = tmp_path / "acs.json"
    write_snapshot(path, snapshot)
    with pytest.raises(ValueError, match="year or dataset"):
        load_snapshot(path, 2023)


def test_snapshot_requires_every_configured_county(tmp_path):
    snapshot = collect_snapshot(FakeClient(), ["17031"])
    path = tmp_path / "acs.json"
    write_snapshot(path, snapshot)
    _, counties, _ = load_snapshot(path, 2024)
    with pytest.raises(ValueError, match="17089"):
        snapshot_evidence(ACSClient(2024), counties, ["17031", "17089"])
