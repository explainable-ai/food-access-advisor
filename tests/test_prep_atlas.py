import sqlite3
import pandas as pd
from data import prep_atlas


def test_normalize_tract_fips_handles_excel_numbers():
    assert prep_atlas._normalize_tract_fips("17003960100.0") == "17003960100"


def test_prepares_rural_database_with_provenance(tmp_path, monkeypatch):
    target = tmp_path / "rural.db"
    monkeypatch.setitem(prep_atlas.REGIONS["rural"], "db_path", target)
    frame = pd.DataFrame({"CensusTract": ["17003960100", "17031990000"], "POP2010": [1560, 999],
                          "LILATracts_1And10": [1, 1], "LILATracts_1And20": [1, 0]})
    count, path = prep_atlas.prepare_region_database(frame, "rural", "LRAM", "atlas.csv")
    assert count == 1 and path == target
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT tract_fips FROM tracts").fetchone()[0] == "17003960100"
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    assert metadata["product"] == "LRAM" and metadata["data_mode"] == "real"
    assert metadata["thresholds"] == "10/20 miles"


def test_region_fails_when_required_threshold_is_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(prep_atlas.REGIONS["urban"], "db_path", tmp_path / "urban.db")
    frame = pd.DataFrame({"CensusTract": ["17031010100"], "POP2010": [1]})
    try:
        prep_atlas.prepare_region_database(frame, "urban", "LRAM", "atlas.csv")
    except ValueError as error:
        assert "tight threshold" in str(error)
    else:
        raise AssertionError("missing Atlas columns should fail closed")
