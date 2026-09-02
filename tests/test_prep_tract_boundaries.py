"""Tests for complete tract-geometry manifest validation."""

import hashlib
import json
import sqlite3

import pytest

from data import prep_tract_boundaries as boundaries


def _digest(geoids):
    return hashlib.sha256("\n".join(sorted(geoids)).encode()).hexdigest()


def _write_database(path, geoids):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tracts (tract_fips TEXT PRIMARY KEY)")
        connection.executemany(
            "INSERT INTO tracts VALUES (?)", [(geoid,) for geoid in geoids]
        )
        connection.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                ("geography_vintage", "2020"),
                ("tract_count", str(len(geoids))),
                ("tract_fips_sha256", _digest(geoids)),
            ],
        )


def _feature(geoid):
    return {
        "type": "Feature",
        "properties": {"tract_fips": geoid},
        "geometry": {"type": "Point", "coordinates": [0, 0]},
    }


def test_database_manifest_reads_every_prepared_tract(tmp_path):
    geoids = {"17063000600", "17091011200", "17091010300"}
    path = tmp_path / "rural.db"
    _write_database(path, geoids)

    actual, metadata = boundaries._database_manifest(path)

    assert actual == geoids
    assert metadata["tract_count"] == "3"
    boundaries._require_manifest("rural", actual, 3, _digest(geoids))


def test_boundary_validation_fails_when_any_prepared_tract_is_missing():
    geoids = {"A", "B"}
    features = {"A": _feature("A")}

    with pytest.raises(ValueError, match="1 prepared tracts lack TIGER boundaries"):
        boundaries._require_boundaries("rural", geoids, features)


def test_scoring_geojson_contains_only_prepared_manifest(tmp_path):
    context = {geoid: _feature(geoid) for geoid in ("A", "B", "C")}
    scoring_geoids = {"A", "C"}
    scoring = {geoid: context[geoid] for geoid in sorted(scoring_geoids)}
    output = tmp_path / "rural_scoring.geojson"

    boundaries._write_geojson(scoring, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert {
        feature["properties"]["tract_fips"] for feature in payload["features"]
    } == scoring_geoids
