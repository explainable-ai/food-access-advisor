"""Unit tests for prepared tract reads and scoring-context validation."""

import hashlib
import inspect
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import access_data  # noqa: E402
from tools.access_data import (  # noqa: E402
    PreparedTractDataError,
    get_all_rural_tracts,
    get_low_access_tracts,
    get_low_access_rural_tracts,
)


def _digest(geoids):
    return hashlib.sha256("\n".join(sorted(geoids)).encode()).hexdigest()


def _write_rural_db(path):
    rows = [
        ("17089960100", 1500, 0, 0, 41.88, -88.47, 100, 20, 100, 5, 0.81, 0.60, 0.09),
        ("17093960100", 900, 0, 0, 41.64, -88.45, 100, 15, 100, 4, 0.40, 0.30, 0.00),
        ("17063960100", 700, 0, 0, 41.20, -88.30, 100, 10, 100, 3, 0.00, 0.00, 0.00),
        ("17197980000", 0, 0, 0, 41.50, -88.10, 0, 0, 0, 0, None, None, 0.00),
    ]
    geoids = [row[0] for row in rows]
    with sqlite3.connect(path) as connection:
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
                households_no_vehicle REAL,
                low_access_population_share REAL,
                low_income_low_access_share REAL,
                no_vehicle_low_access_share REAL
            )"""
        )
        connection.executemany(
            "INSERT INTO tracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("geography_vintage", "2020"),
                ("tract_count", str(len(rows))),
                ("tract_fips_sha256", _digest(geoids)),
                ("rural_food_access_metric", "low_income_low_access_share_10mi"),
            ],
        )
    return geoids


def test_get_low_access_rural_tracts_has_no_region_argument():
    sig = inspect.signature(get_low_access_rural_tracts)
    assert list(sig.parameters) == ["limit"]


def test_urban_candidates_filter_chicago_before_limit(tmp_path, monkeypatch):
    database = tmp_path / "urban.db"
    database.touch()
    monkeypatch.setattr(access_data, "_prepared_database_path", lambda: database)
    monkeypatch.setattr(
        access_data,
        "_read_database",
        lambda path, urban_context=False: [
            {"tract_fips": "suburb", "is_chicago": False},
            {"tract_fips": "chicago-1", "is_chicago": True},
            {"tract_fips": "chicago-2", "is_chicago": True},
        ],
    )

    rows = get_low_access_tracts(limit=1)

    assert [row["tract_fips"] for row in rows] == ["chicago-1"]


def test_missing_rural_artifact_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(access_data, "RURAL_DB_PATH", tmp_path / "missing.db")
    monkeypatch.delenv("TRACT_DATA_BUCKET", raising=False)
    monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)

    with pytest.raises(PreparedTractDataError, match="rural-fringe"):
        get_low_access_rural_tracts()


def test_rural_candidates_use_continuous_low_income_access_share(tmp_path, monkeypatch):
    path = tmp_path / "rural.db"
    geoids = _write_rural_db(path)
    monkeypatch.setattr(access_data, "RURAL_DB_PATH", path)
    monkeypatch.setitem(
        access_data.PILOT_RURAL_COUNTY, "expected_atlas_tract_count", len(geoids)
    )
    monkeypatch.setitem(
        access_data.PILOT_RURAL_COUNTY,
        "expected_atlas_tract_fips_sha256",
        _digest(geoids),
    )

    rows = get_low_access_rural_tracts(limit=25)

    assert [row["tract_fips"] for row in rows] == [
        "17089960100",
        "17093960100",
    ]
    assert rows[0]["low_income_low_access_share"] == 0.60
    assert all(row["population"] > 0 for row in rows)
    assert all(row["data_mode"] == "real" for row in rows)


def test_rural_candidates_reject_unapproved_access_metric(tmp_path, monkeypatch):
    path = tmp_path / "rural.db"
    geoids = _write_rural_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = ?",
            ("not_applicable", "rural_food_access_metric"),
        )
    monkeypatch.setattr(access_data, "RURAL_DB_PATH", path)
    monkeypatch.setitem(
        access_data.PILOT_RURAL_COUNTY, "expected_atlas_tract_count", len(geoids)
    )
    monkeypatch.setitem(
        access_data.PILOT_RURAL_COUNTY,
        "expected_atlas_tract_fips_sha256",
        _digest(geoids),
    )

    with pytest.raises(PreparedTractDataError, match="approved continuous access metric"):
        get_low_access_rural_tracts()


def test_get_all_rural_tracts_validates_complete_manifest(tmp_path, monkeypatch):
    path = tmp_path / "rural.db"
    geoids = _write_rural_db(path)
    monkeypatch.setattr(access_data, "RURAL_DB_PATH", path)
    monkeypatch.setitem(
        access_data.PILOT_RURAL_COUNTY, "expected_atlas_tract_count", len(geoids)
    )
    monkeypatch.setitem(
        access_data.PILOT_RURAL_COUNTY,
        "expected_atlas_tract_fips_sha256",
        _digest(geoids),
    )

    rows = get_all_rural_tracts()

    assert {row["tract_fips"] for row in rows} == set(geoids)


def test_urban_context_overlay_requires_complete_transportation(tmp_path, monkeypatch):
    context_path = tmp_path / "context.json"
    context_path.write_text(
        json.dumps(
            {
                "context_format": access_data.URBAN_CONTEXT_FORMAT,
                "tract_count": 1,
                "chicago_tract_count": 1,
                "transportation_scored_tract_count": 1,
                "records": [
                    {
                        "tract_fips": "17031010100",
                        "is_chicago": True,
                        "community_area": "North Lawndale",
                        "food_insecurity_rate": 0.8,
                        "transit_burden": 0.7,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(access_data, "URBAN_CONTEXT_PATH", context_path)
    rows = [{"tract_fips": "17031010100", "population": 1000}]

    overlaid = access_data._overlay_urban_context(rows, require_complete=True)

    assert overlaid[0]["community_area"] == "North Lawndale"
    assert overlaid[0]["transit_burden"] == 0.7


def test_urban_context_rejects_missing_transportation(tmp_path, monkeypatch):
    context_path = tmp_path / "context.json"
    context_path.write_text(
        json.dumps(
            {
                "context_format": access_data.URBAN_CONTEXT_FORMAT,
                "tract_count": 1,
                "chicago_tract_count": 1,
                "transportation_scored_tract_count": 0,
                "records": [
                    {
                        "tract_fips": "17031010100",
                        "is_chicago": True,
                        "food_insecurity_rate": 0.8,
                        "transit_burden": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(access_data, "URBAN_CONTEXT_PATH", context_path)

    with pytest.raises(PreparedTractDataError, match="missing transportation"):
        access_data._overlay_urban_context(
            [{"tract_fips": "17031010100", "population": 1000}],
            require_complete=True,
        )
