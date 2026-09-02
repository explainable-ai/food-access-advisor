"""Tests for the complete, prepared Cook County heatmap score surface."""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import api.main as api_main  # noqa: E402
import tools.access_data as access_data  # noqa: E402
import tools.resource_cache as resource_cache  # noqa: E402
from tools.gap_scorer import PriorityWeights, score_all_gaps  # noqa: E402


client = TestClient(api_main.app)


def _tract(fips, population, low_access=0):
    return {
        "tract_fips": fips,
        "population": population,
        "low_access_half_mile": low_access,
        "low_access_one_mile": low_access,
        "centroid_lat": 41.8,
        "centroid_lon": -87.6,
        "poverty_universe": 100,
        "population_below_poverty": 20,
        "households_total": 100,
        "households_no_vehicle": 10,
    }


def test_get_all_tracts_includes_non_low_access_rows(tmp_path, monkeypatch):
    database = tmp_path / "atlas.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE tracts (
                tract_fips TEXT PRIMARY KEY,
                population INTEGER,
                low_access_half_mile INTEGER,
                low_access_one_mile INTEGER,
                centroid_lat REAL,
                centroid_lon REAL,
                poverty_universe REAL,
                population_below_poverty REAL,
                households_total REAL,
                households_no_vehicle REAL
            )"""
        )
        connection.executemany(
            "INSERT INTO tracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("A", 2000, 1, 1, 41.8, -87.6, 100, 20, 100, 10),
                ("B", 1000, 0, 0, 41.9, -87.7, 100, 15, 100, 8),
            ],
        )
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("acs_vintage", "2024"),
                ("acs_dataset", "2024/acs/acs5"),
                ("acs_geography_vintage", "2020"),
            ],
        )
    monkeypatch.setattr(access_data, "DB_PATH", database)

    result = access_data.get_all_tracts()

    assert {row["tract_fips"] for row in result} == {"A", "B"}


def test_score_all_gaps_returns_every_tract_without_sensitivity_sweep():
    tracts = [_tract("B", 1000), _tract("A", 2000, low_access=1), _tract("C", 500)]
    result = score_all_gaps(tracts, [])

    assert {row["tract_fips"] for row in result} == {"A", "B", "C"}
    assert [row["rank"] for row in result] == [1, 2, 3]
    assert all("sensitivity" not in row for row in result)


def test_default_weights_match_the_approved_user_scenario():
    assert PriorityWeights().normalized() == {
        "food_access_gap": 0.25,
        "poverty": 0.20,
        "no_vehicle": 0.15,
        "population_served": 0.20,
        "transit_burden": 0.10,
        "existing_coverage": 0.10,
    }


def test_site_tract_scores_returns_complete_prepared_surface(monkeypatch):
    prepared = [_tract("A", 2000, low_access=1), _tract("B", 1000), _tract("C", 500)]
    monkeypatch.setattr(api_main, "get_all_tracts", lambda: prepared)
    monkeypatch.setattr(api_main, "load_resource_cache", lambda scope, **kwargs: [])

    response = client.get("/api/site-advisor/tract-scores")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == len(prepared)
    assert {row["tract_fips"] for row in body} == {"A", "B", "C"}
    assert body[0]["weights_used"]["population_served"] == 0.2
    assert body[0]["sensitivity"] == {}


def test_get_all_tracts_rejects_atlas_database_without_acs(tmp_path, monkeypatch):
    database = tmp_path / "atlas_only.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE tracts (
                tract_fips TEXT PRIMARY KEY,
                population INTEGER,
                low_access_half_mile INTEGER,
                low_access_one_mile INTEGER,
                centroid_lat REAL,
                centroid_lon REAL
            )"""
        )
        connection.execute("INSERT INTO tracts VALUES ('A', 1000, 1, 1, 41.8, -87.6)")
    monkeypatch.setattr(access_data, "DB_PATH", database)

    try:
        access_data.get_all_tracts()
    except access_data.PreparedTractDataError as error:
        assert "missing ACS columns" in str(error)
    else:
        raise AssertionError("Atlas-only data must not power the heatmap")


def test_get_all_tracts_rejects_malformed_database_as_prepared_data_failure(
    tmp_path, monkeypatch
):
    database = tmp_path / "malformed.db"
    database.touch()
    monkeypatch.setattr(access_data, "DB_PATH", database)

    try:
        access_data.get_all_tracts()
    except access_data.PreparedTractDataError as error:
        assert "unreadable or has an invalid schema" in str(error)
    else:
        raise AssertionError("Malformed data must return a prepared-data failure")


def test_get_all_tracts_materializes_prepared_database_from_s3(tmp_path, monkeypatch):
    source = tmp_path / "prepared.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            """CREATE TABLE tracts (
                tract_fips TEXT PRIMARY KEY,
                population INTEGER,
                low_access_half_mile INTEGER,
                low_access_one_mile INTEGER,
                centroid_lat REAL,
                centroid_lon REAL,
                poverty_universe REAL,
                population_below_poverty REAL,
                households_total REAL,
                households_no_vehicle REAL
            )"""
        )
        connection.execute(
            "INSERT INTO tracts VALUES ('A', 1000, 1, 1, 41.8, -87.6, 100, 20, 100, 10)"
        )
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("acs_vintage", "2024"),
                ("acs_dataset", "2024/acs/acs5"),
                ("acs_geography_vintage", "2020"),
            ],
        )

    class FakeS3:
        def download_fileobj(self, bucket, key, destination):
            assert bucket == "evidence-bucket"
            assert key == access_data.DEFAULT_DATABASE_KEY
            destination.write(source.read_bytes())

    target = tmp_path / "cache" / "atlas_pilot_city.db"
    monkeypatch.setattr(access_data, "DB_PATH", tmp_path / "not-packaged.db")
    monkeypatch.setenv("EVIDENCE_BUCKET", "evidence-bucket")
    monkeypatch.setenv("TRACT_DATA_CACHE_PATH", str(target))
    monkeypatch.setattr(access_data.boto3, "client", lambda service: FakeS3())

    result = access_data.get_all_tracts()

    assert [row["tract_fips"] for row in result] == ["A"]
    assert target.exists()


def test_complete_resource_cache_requires_county_coverage(tmp_path, monkeypatch):
    payload = {
        "scope": "urban",
        "coverage_bbox": [41.60, -87.85, 42.05, -87.52],
        "resources": [],
    }
    (tmp_path / "urban.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("RESOURCE_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("RESOURCE_CACHE_BUCKET", raising=False)
    monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)

    try:
        resource_cache.load_resource_cache("urban", require_complete_coverage=True)
    except resource_cache.ResourceCacheError as error:
        assert "does not cover required urban bounds" in str(error)
    else:
        raise AssertionError("partial resource coverage must not power the heatmap")


def test_site_tract_scores_never_falls_back_to_sample_rows(monkeypatch):
    def unavailable():
        raise api_main.PreparedTractDataError("prepared database missing")

    monkeypatch.setattr(api_main, "get_all_tracts", unavailable)
    monkeypatch.setattr(api_main, "load_resource_cache", lambda scope, **kwargs: [])

    response = client.get("/api/site-advisor/tract-scores")

    assert response.status_code == 503
    assert "Prepared heatmap data unavailable" in response.json()["detail"]
