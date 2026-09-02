import sqlite3

import pandas as pd
import pytest

from data import prep_hta


def _write_database(path, geoids, vintage="2020"):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tracts (tract_fips TEXT PRIMARY KEY)")
        connection.executemany("INSERT INTO tracts VALUES (?)", [(item,) for item in geoids])
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO metadata VALUES ('geography_vintage', ?)", (vintage,))


def _source_frame():
    return pd.DataFrame(
        {
            "blkgrp": ["170310101001", "170310101002", "170310102001"],
            "households": ["100", "300", "0"],
            "ht_80ami": ["40", "60", ""],
            "h_80ami": ["30", "35", ""],
            "t_80ami": ["10", "20", ""],
            "t_cost_80ami": ["8000", "12000", ""],
            "autos_per_hh_80ami": ["1", "2", ""],
            "vmt_per_hh_80ami": ["6000", "10000", ""],
        }
    )


def test_tract_aggregation_is_household_weighted_and_preserves_missing():
    frame = _source_frame()
    frame["block_group_geoid"] = frame["blkgrp"]
    frame["tract_fips"] = frame["blkgrp"].str[:11]
    for column in ["households", *prep_hta.SOURCE_METRICS]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    rows = prep_hta.aggregate_to_tracts(
        frame, {"17031010100", "17031010200"}
    )

    assert rows[0][0] == "17031010100"
    assert rows[0][1] == pytest.approx(55.0)
    assert rows[0][2] == pytest.approx(33.75)
    assert rows[0][3] == pytest.approx(17.5)
    assert rows[0][4] == pytest.approx(11000.0)
    assert rows[0][-3:] == (2, 2, 400.0)
    assert rows[1][0] == "17031010200"
    assert rows[1][1:7] == (None, None, None, None, None, None)
    assert rows[1][-3:] == (1, 0, 0.0)


def test_source_rejects_missing_required_column(tmp_path):
    path = tmp_path / "hta.csv"
    _source_frame().drop(columns=["t_80ami"]).to_csv(path, index=False)

    with pytest.raises(ValueError, match="t_80ami"):
        prep_hta.load_source(path)


def test_source_rejects_duplicate_block_groups(tmp_path):
    path = tmp_path / "hta.csv"
    frame = _source_frame()
    frame.loc[1, "blkgrp"] = frame.loc[0, "blkgrp"]
    frame.to_csv(path, index=False)

    with pytest.raises(ValueError, match="duplicate block-group GEOIDs"):
        prep_hta.load_source(path)


def test_aggregation_rejects_a_prepared_tract_without_source_rows():
    frame = _source_frame()
    frame["block_group_geoid"] = frame["blkgrp"]
    frame["tract_fips"] = frame["blkgrp"].str[:11]
    for column in ["households", *prep_hta.SOURCE_METRICS]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    with pytest.raises(ValueError, match="17031099900"):
        prep_hta.aggregate_to_tracts(frame, {"17031099900"})


def test_database_requires_matching_geography_vintage(tmp_path):
    path = tmp_path / "atlas.db"
    _write_database(path, ["17031010100"], vintage="2010")

    with pytest.raises(ValueError, match="explicit crosswalk"):
        prep_hta._database_geoids_and_vintage(path)


def test_writes_separate_features_and_lineage_metadata(tmp_path):
    path = tmp_path / "atlas.db"
    _write_database(path, ["17031010100"])
    rows = [
        ("17031010100", 55.0, 33.75, 17.5, 11000.0, 1.75, 9000.0, 2, 2, 400.0)
    ]

    prep_hta.write_region_features(
        path,
        rows,
        source_path="hta.csv",
        source_sha256="abc",
        source_rows=9896,
    )

    with sqlite3.connect(path) as connection:
        stored = connection.execute("SELECT * FROM hta_tract_features").fetchone()
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    assert stored == rows[0]
    assert metadata["hta_scoring_metric"] == "t_80ami"
    assert metadata["hta_tract_aggregation"] == "household_weighted_positive_households"
    assert metadata["hta_source_archive_sha256"] == prep_hta.CANONICAL_ARCHIVE_SHA256
