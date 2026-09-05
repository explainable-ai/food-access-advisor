import json
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from data.s3_prepare_scores import (
    VerifiedS3Store,
    _feature_and_score_payloads,
    safe_extract,
    sha256_file,
)


def test_direct_script_entry_point_can_import_project_modules():
    script = Path(__file__).parents[1] / "data" / "s3_prepare_scores.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=script.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--evidence-date" in result.stdout


class FakeS3:
    def __init__(self, source, checksum):
        self.source = Path(source)
        self.checksum = checksum

    def head_object(self, Bucket, Key):
        return {
            "Metadata": {"sha256": self.checksum},
            "ContentLength": self.source.stat().st_size,
            "VersionId": "v1",
        }

    def download_file(self, bucket, key, destination):
        Path(destination).write_bytes(self.source.read_bytes())


def test_verified_download_accepts_matching_object(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"ok": true}', encoding="utf-8")
    store = VerifiedS3Store(FakeS3(source, sha256_file(source)), "evidence")
    destination = tmp_path / "downloaded.json"

    result = store.download("raw/input.json", destination)

    assert destination.read_bytes() == source.read_bytes()
    assert result["version_id"] == "v1"


def test_verified_download_rejects_checksum_mismatch(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("bad", encoding="utf-8")
    store = VerifiedS3Store(FakeS3(source, "0" * 64), "evidence")

    with pytest.raises(ValueError, match="checksum mismatch"):
        store.download("raw/input.json", tmp_path / "downloaded.json")


def test_safe_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("../outside.txt", "no")

    with pytest.raises(ValueError, match="unsafe ZIP member"):
        safe_extract(archive, tmp_path / "out")


def _database(path, rows):
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE tracts (
               tract_fips TEXT PRIMARY KEY, population INTEGER,
               low_access_half_mile INTEGER, low_access_one_mile INTEGER,
               centroid_lat REAL, centroid_lon REAL,
               poverty_universe REAL, population_below_poverty REAL,
               households_total REAL, households_no_vehicle REAL,
               low_access_population_share REAL,
               low_income_low_access_share REAL,
               no_vehicle_low_access_share REAL)"""
        )
        connection.executemany(
            "INSERT INTO tracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO metadata VALUES ('geography_vintage', '2020')")


def test_rural_feature_surface_and_scores_are_deterministic(tmp_path, monkeypatch):
    database = tmp_path / "rural.db"
    rows = []
    for index in range(2):
        rows.append((
            f"17089000{index:03d}", 1000 + index, 1, 1, 41.0 + index / 100,
            -88.0, 100, 20, 100, 10, 0.8, 0.6, 0.1,
        ))
    _database(database, rows)
    resources = tmp_path / "rural.json"
    resources.write_text(json.dumps({
        "scope": "rural", "generated_at": "2026-09-05T00:00:00+00:00",
        "coverage_bboxes": [
            area["bbox"] for area in __import__(
                "data.s3_prepare_scores", fromlist=["PILOT_RURAL_COUNTY"]
            ).PILOT_RURAL_COUNTY["resource_areas"]
        ],
        "resources": [{"name": "Market", "kind": "grocery", "lat": 41.0, "lon": -88.0}],
    }), encoding="utf-8")
    monkeypatch.setitem(__import__("data.s3_prepare_scores", fromlist=["PILOT_RURAL_COUNTY"]).PILOT_RURAL_COUNTY, "expected_atlas_tract_count", 2)

    features, scores = _feature_and_score_payloads(
        "rural", database, resources, "2026-09-05T00:00:00+00:00"
    )

    assert features["tract_count"] == 2
    assert scores["tract_count"] == 2
    assert [row["rank"] for row in scores["scores"]] == [1, 2]
    assert scores["weights"]["food_access_gap"] == 0.25

    # This fails with WinError 32 when the SQLite connection remains open.
    moved = tmp_path / "released.db"
    database.rename(moved)
    assert moved.exists()
