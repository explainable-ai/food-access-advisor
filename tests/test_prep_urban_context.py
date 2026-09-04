"""Tests for the prepared Chicago scoring context."""

import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.prep_urban_context import CONTEXT_FORMAT, build_context  # noqa: E402


def _write_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE tracts (
                tract_fips TEXT PRIMARY KEY,
                centroid_lat REAL,
                centroid_lon REAL
            )"""
        )
        connection.executemany(
            "INSERT INTO tracts VALUES (?, ?, ?)",
            [
                ("17031010100", 41.88, -87.63),
                ("17031010200", 42.05, -87.75),
            ],
        )


def _write_food_snapshot(path, include_suburban=True):
    features = [
        {
            "attributes": {
                "GEOID": "17031010100",
                "Rate200FPL": 72,
                "Count200FPL": 720,
                "PopPovDetermined": 1000,
                "CookCountyCommunityArea": "North Lawndale",
            }
        }
    ]
    if include_suburban:
        features.append(
            {
                "attributes": {
                    "GEOID": "17031010200",
                    "Rate200FPL": 18,
                    "Count200FPL": 180,
                    "PopPovDetermined": 1000,
                    "CookCountyCommunityArea": "NA",
                }
            }
        )
    path.write_text(json.dumps({"features": features}), encoding="utf-8")


def _write_gtfs(path):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday\n"
            "WK,1,1,1,1,1,0,0\n",
        )
        archive.writestr("trips.txt", "route_id,service_id,trip_id\n82,WK,T1\n")
        archive.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,08:00:00,08:00:00,S1,1\n",
        )
        archive.writestr(
            "stops.txt",
            "stop_id,stop_name,stop_lat,stop_lon\nS1,Test stop,41.88,-87.63\n",
        )


def test_build_context_includes_real_transportation_for_chicago(tmp_path):
    database = tmp_path / "tracts.db"
    food = tmp_path / "food.json"
    gtfs = tmp_path / "cta.zip"
    _write_database(database)
    _write_food_snapshot(food)
    _write_gtfs(gtfs)

    payload = build_context(database, food, gtfs, generated_at="2026-09-04T00:00:00Z")

    assert payload["context_format"] == CONTEXT_FORMAT
    assert payload["tract_count"] == 2
    assert payload["chicago_tract_count"] == 1
    assert payload["transportation_scored_tract_count"] == 1
    chicago, suburb = payload["records"]
    assert chicago["community_area"] == "North Lawndale"
    assert chicago["food_insecurity_rate"] == 0.72
    assert chicago["transit_nearest_stop_miles"] == 0
    assert chicago["transit_route_count"] == 1
    assert chicago["transit_weekday_trips"] == 1
    assert chicago["transit_burden"] is not None
    assert suburb["is_chicago"] is False
    assert suburb["transit_burden"] is None


def test_build_context_fails_when_food_snapshot_omits_prepared_tract(tmp_path):
    database = tmp_path / "tracts.db"
    food = tmp_path / "food.json"
    gtfs = tmp_path / "cta.zip"
    _write_database(database)
    _write_food_snapshot(food, include_suburban=False)
    _write_gtfs(gtfs)

    with pytest.raises(ValueError, match="missing 1 prepared tracts"):
        build_context(database, food, gtfs)
