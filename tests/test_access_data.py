"""Unit tests for prepared rural low-access tract reads."""

import inspect
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import access_data  # noqa: E402
from tools.access_data import (  # noqa: E402
    PreparedTractDataError,
    get_low_access_rural_tracts,
)


def _write_rural_db(path):
    with sqlite3.connect(path) as connection:
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
        connection.executemany(
            "INSERT INTO tracts VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("17089960100", 1500, 1, 1, 41.88, -88.47),
                ("17093960100", 900, 1, 0, 41.64, -88.45),
            ],
        )


def test_get_low_access_rural_tracts_has_no_region_argument():
    sig = inspect.signature(get_low_access_rural_tracts)
    assert list(sig.parameters) == ["limit"]


def test_missing_rural_artifact_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(access_data, "RURAL_DB_PATH", tmp_path / "missing.db")
    monkeypatch.delenv("TRACT_DATA_BUCKET", raising=False)
    monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)

    with pytest.raises(PreparedTractDataError, match="rural-fringe"):
        get_low_access_rural_tracts()


def test_reads_prepared_rural_rows_and_respects_limit(tmp_path, monkeypatch):
    path = tmp_path / "rural.db"
    _write_rural_db(path)
    monkeypatch.setattr(access_data, "RURAL_DB_PATH", path)

    rows = get_low_access_rural_tracts(limit=1)

    assert len(rows) == 1
    assert rows[0]["tract_fips"] == "17089960100"
    assert rows[0]["data_mode"] == "real"
