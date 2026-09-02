import sqlite3
import pandas as pd
from data import prep_atlas


def test_normalize_tract_fips_handles_excel_numbers():
    assert prep_atlas._normalize_tract_fips("17089960100.0") == "17089960100"


def test_prepares_only_rural_tracts_from_all_configured_counties(tmp_path, monkeypatch):
    target = tmp_path / "rural.db"
    monkeypatch.setitem(prep_atlas.REGIONS["rural"], "db_path", target)
    frame = pd.DataFrame(
        {
            "CensusTract": [
                "17089960100",  # Kane, rural
                "17093960100",  # Kendall, rural
                "17031990000",  # Cook, urban: excluded
                "17003960100",  # Alexander: excluded
            ],
            "POP2010": [1560, 980, 999, 500],
            "Urban": [0, 0, 1, 0],
            "LILATracts_1And10": [1, 1, 1, 1],
            "LILATracts_1And20": [1, 0, 0, 1],
        }
    )
    count, path = prep_atlas.prepare_region_database(
        frame, "rural", "LRAM", "atlas.csv"
    )
    assert count == 2 and path == target
    with sqlite3.connect(target) as connection:
        geoids = {
            row[0] for row in connection.execute("SELECT tract_fips FROM tracts")
        }
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    assert geoids == {"17089960100", "17093960100"}
    assert metadata["product"] == "LRAM" and metadata["data_mode"] == "real"
    assert metadata["thresholds"] == "10/20 miles"
    assert metadata["rural_only"] == "true"
    assert metadata["rural_indicator"] == "Urban=0"
    assert metadata["atlas_excluded_tract_fips"] == ""
    assert metadata["atlas_exclusion_reason"] == "not_applicable"


def test_rural_prep_requires_usda_classification(tmp_path, monkeypatch):
    monkeypatch.setitem(prep_atlas.REGIONS["rural"], "db_path", tmp_path / "rural.db")
    frame = pd.DataFrame(
        {
            "CensusTract": ["17089960100"],
            "POP2010": [1],
            "LILATracts_1And10": [1],
            "LILATracts_1And20": [1],
        }
    )
    try:
        prep_atlas.prepare_region_database(frame, "rural", "LRAM", "atlas.csv")
    except ValueError as error:
        assert "USDA urban/rural indicator" in str(error)
    else:
        raise AssertionError("rural prep must fail without an official classification field")


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
        prep_atlas.REGIONS["urban"]["config"], "expected_atlas_tract_count", 2
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


def test_current_urban_atlas_requires_authoritative_tract_manifest(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(prep_atlas.REGIONS["urban"], "db_path", tmp_path / "urban.db")
    monkeypatch.setitem(
        prep_atlas.REGIONS["urban"]["config"], "expected_atlas_tract_count", 1
    )
    monkeypatch.setitem(
        prep_atlas.REGIONS["urban"]["config"],
        "expected_atlas_tract_fips_sha256",
        "0" * 64,
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
        assert "does not match the authoritative 2020 manifest" in str(error)
    else:
        raise AssertionError("A wrong Cook County tract set must fail closed")



def _write_split_sram_bundle(root):
    general = pd.DataFrame(
        {
            "CensusTract20": ["17031010100", "17089960100"],
            "State": ["Illinois", "Illinois"],
            "County20": ["Cook", "Kane"],
            "Urban": ["1", "0"],
            "POP2020": ["1000", "700"],
        }
    )
    driving = pd.DataFrame(
        {
            "CensusTract20": ["17031010100", "17089960100"],
            "DD_SRAM_LILATracts_halfAnd10": ["1", "0"],
            "DD_SRAM_LILATracts_1And10": ["1", "1"],
            "DD_SRAM_LILATracts_1And20": ["0", "1"],
            "DD_SRAM_lapop10share": ["0.00", "0.81"],
            "DD_SRAM_lalowi10share": ["0.00", "0.60"],
            "DD_SRAM_lahunv10share": ["0.00", "0.09"],
        }
    )
    straight = pd.DataFrame(
        {
            "CensusTract20": ["17031010100", "17089960100"],
            "SD_SRAM_LILATracts_halfAnd10": ["0", "1"],
            "SD_SRAM_LILATracts_1And10": ["0", "0"],
            "SD_SRAM_LILATracts_1And20": ["1", "0"],
        }
    )
    general.to_csv(
        root / prep_atlas.SRAM_FILES["general"], index=False, encoding="cp1252"
    )
    driving.to_csv(
        root / prep_atlas.SRAM_FILES["driving"], index=False, encoding="cp1252"
    )
    straight.to_csv(
        root / prep_atlas.SRAM_FILES["straight"], index=False, encoding="cp1252"
    )


def test_loads_split_sram_bundle_with_driving_distance_by_default(tmp_path):
    _write_split_sram_bundle(tmp_path)
    frame, method, sources = prep_atlas._load_data(tmp_path, "SRAM")

    assert method == "sram_driving_distance"
    assert len(sources) == 2
    assert list(frame["CensusTract20"]) == ["17031010100", "17089960100"]
    assert "DD_SRAM_LILATracts_halfAnd10" in frame
    assert "SD_SRAM_LILATracts_halfAnd10" not in frame


def test_split_sram_can_explicitly_use_straight_line_distance(tmp_path):
    _write_split_sram_bundle(tmp_path)
    frame, method, _ = prep_atlas._load_data(
        tmp_path, "SRAM", distance_method="straight"
    )

    assert method == "sram_straight_distance"
    assert "SD_SRAM_LILATracts_halfAnd10" in frame
    assert "DD_SRAM_LILATracts_halfAnd10" not in frame


def test_split_sram_rejects_incomplete_tract_join(tmp_path):
    _write_split_sram_bundle(tmp_path)
    driving_path = tmp_path / prep_atlas.SRAM_FILES["driving"]
    driving = pd.read_csv(driving_path, dtype=str, encoding="cp1252").iloc[:1]
    driving.to_csv(driving_path, index=False, encoding="cp1252")

    try:
        prep_atlas._load_data(tmp_path, "SRAM")
    except ValueError as error:
        assert "SRAM tract sets do not match" in str(error)
    else:
        raise AssertionError("An incomplete SRAM tract join must fail closed")


def test_sram_database_records_access_method_and_requires_coordinates(
    tmp_path, monkeypatch
):
    target = tmp_path / "urban.db"
    monkeypatch.setitem(prep_atlas.REGIONS["urban"], "db_path", target)
    monkeypatch.setitem(
        prep_atlas.REGIONS["urban"]["config"], "expected_atlas_tract_count", 1
    )
    expected_digest = prep_atlas.hashlib.sha256(b"17031010100").hexdigest()
    monkeypatch.setitem(
        prep_atlas.REGIONS["urban"]["config"],
        "expected_atlas_tract_fips_sha256",
        expected_digest,
    )
    frame = pd.DataFrame(
        {
            "CensusTract20": ["17031010100"],
            "POP2020": ["1000"],
            "Urban": ["1"],
            "DD_SRAM_LILATracts_halfAnd10": ["1"],
            "DD_SRAM_LILATracts_1And10": ["1"],
        }
    )

    try:
        prep_atlas.prepare_region_database(
            frame,
            "urban",
            "SRAM",
            "sram_2025",
            require_coordinates=True,
        )
    except ValueError as error:
        assert "tracts lack 2020 centroids" in str(error)
    else:
        raise AssertionError("Current SRAM preparation must require 2020 coordinates")

    prep_atlas.prepare_region_database(
        frame,
        "urban",
        "SRAM",
        "sram_2025",
        {"17031010100": (41.9, -87.7)},
        access_method="sram_driving_distance",
        source_files=("general.csv", "driving.csv"),
        require_coordinates=True,
    )
    with sqlite3.connect(target) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    assert metadata["access_method"] == "sram_driving_distance"
    assert metadata["source_files"] == "general.csv|driving.csv"
    assert metadata["tract_count"] == "1"
    assert metadata["tract_fips_sha256"] == expected_digest



def test_cook_boundary_and_sram_manifests_are_explicitly_distinct():
    config = prep_atlas.REGIONS["urban"]["config"]

    assert config["expected_tract_count"] == 1332
    assert config["expected_atlas_tract_count"] == 1331
    assert config["atlas_excluded_tract_fips"] == ["17031990000"]
    assert (
        config["expected_atlas_tract_fips_sha256"]
        == "aac4ceecdc0e4ea16437ad9a6ff592b863376a52a6b48ee3e07f97ebf874ed0f"
    )


def test_sram_rural_database_retains_continuous_driving_distance_shares(
    tmp_path, monkeypatch
):
    target = tmp_path / "rural.db"
    config = prep_atlas.REGIONS["rural"]["config"]
    monkeypatch.setitem(prep_atlas.REGIONS["rural"], "db_path", target)
    monkeypatch.setitem(config, "expected_atlas_tract_count", 1)
    geoid = "17063000600"
    expected_digest = prep_atlas.hashlib.sha256(geoid.encode()).hexdigest()
    monkeypatch.setitem(
        config, "expected_atlas_tract_fips_sha256", expected_digest
    )
    frame = pd.DataFrame(
        {
            "CensusTract20": [geoid],
            "POP2020": ["2826"],
            "Urban": ["0"],
            "DD_SRAM_LILATracts_1And10": ["0"],
            "DD_SRAM_LILATracts_1And20": ["0"],
            "DD_SRAM_lapop10share": ["0.81"],
            "DD_SRAM_lalowi10share": ["0.60"],
            "DD_SRAM_lahunv10share": ["0.09"],
        }
    )

    prep_atlas.prepare_region_database(
        frame,
        "rural",
        "SRAM",
        "sram_2025",
        {geoid: (41.20, -88.30)},
        access_method="sram_driving_distance",
        source_files=("general.csv", "driving.csv"),
        require_coordinates=True,
    )

    with sqlite3.connect(target) as connection:
        row = connection.execute(
            """SELECT low_access_population_share,
                      low_income_low_access_share,
                      no_vehicle_low_access_share
               FROM tracts WHERE tract_fips = ?""",
            (geoid,),
        ).fetchone()
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))

    assert row == (0.81, 0.60, 0.09)
    assert (
        metadata["rural_food_access_metric"]
        == "low_income_low_access_share_10mi"
    )
