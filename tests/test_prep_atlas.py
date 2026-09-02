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


def test_nan_cells_use_safe_defaults_and_centroid_fallback(tmp_path, monkeypatch):
    target = tmp_path / "urban.db"
    monkeypatch.setitem(prep_atlas.REGIONS["urban"], "db_path", target)
    frame = pd.DataFrame({"CensusTract": ["17031010100"], "POP2010": [float("nan")],
                          "LILATracts_half": [float("nan")], "LILATracts_1And10": [float("nan")],
                          "Latitude": [float("nan")], "Longitude": [float("nan")]})
    prep_atlas.prepare_region_database(frame, "urban", "LRAM", "atlas.csv",
                                       {"17031010100": (41.9, -87.7)})
    with sqlite3.connect(target) as connection:
        row = connection.execute("SELECT population, low_access_half_mile, low_access_one_mile, centroid_lat, centroid_lon FROM tracts").fetchone()
    assert row == (0, 0, 0, 41.9, -87.7)


def test_current_urban_atlas_requires_expected_cook_county_tract_count(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(prep_atlas.REGIONS["urban"], "db_path", tmp_path / "urban.db")
    monkeypatch.setitem(
        prep_atlas.REGIONS["urban"]["config"], "expected_tract_count", 2
    )
    frame = pd.DataFrame(
        {
            "CensusTract": ["17031010100"],
            "POP2020": [1000],
            "LILATracts_half": [1],
            "LILATracts_1And10": [1],
        }
    )

    try:
        prep_atlas.prepare_region_database(frame, "urban", "SRAM", "atlas.csv")
    except ValueError as error:
        assert "expected 2 2020 tracts, found 1" in str(error)
    else:
        raise AssertionError("A partial current Cook County Atlas extract must fail closed")
