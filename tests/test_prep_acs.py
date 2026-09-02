import sqlite3
from datetime import datetime, timezone

from data.prep_acs import _tract_digest, enrich_database
from data_sources.contracts import DataQualityReport, EvidenceStatus, EvidenceValue, SourceCitation, TractEvidence


def make_evidence(geoid="17031010100", geography_vintage="2020"):
    citation = SourceCitation(source_name="Census", dataset_name="ACS", official_url="https://api.census.gov",
        vintage="2024", retrieved_at=datetime.now(timezone.utc), geographic_level="tract")
    values = {name: EvidenceValue(field=name, value=value) for name, value in {
        "poverty_universe": 900, "population_below_poverty": 225,
        "households_total": 400, "households_no_vehicle": 80}.items()}
    return TractEvidence(tract_geoid=geoid, geography_vintage=geography_vintage, source_citations=[citation],
        quality=DataQualityReport(status=EvidenceStatus.COMPLETE), values=values)


def make_database(path, geoids=("17031010100",), vintage="2020"):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tracts (tract_fips TEXT PRIMARY KEY, population INTEGER, low_access_half_mile INTEGER, low_access_one_mile INTEGER, centroid_lat REAL, centroid_lon REAL)")
        connection.executemany("INSERT INTO tracts VALUES (?, 1000, 1, 1, 41.0, -87.0)", [(geoid,) for geoid in geoids])
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO metadata VALUES ('geography_vintage', ?)", (vintage,))


def test_enrich_database_adds_real_acs_values_and_metadata(tmp_path):
    path = tmp_path / "atlas.db"
    make_database(path)
    assert enrich_database(path, [make_evidence()], 2024) == 1
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT poverty_universe, population_below_poverty, households_total, households_no_vehicle FROM tracts").fetchone()
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    assert row == (900.0, 225.0, 400.0, 80.0)
    assert metadata["acs_vintage"] == "2024"
    assert metadata["acs_geography_vintage"] == "2020"
    assert metadata["tract_count"] == "1"
    assert metadata["tract_fips_sha256"] == _tract_digest(("17031010100",))


def test_incomplete_snapshot_rolls_back_old_values_and_vintage(tmp_path):
    path = tmp_path / "atlas.db"
    make_database(path, ("17031010100", "17031010200"))
    with sqlite3.connect(path) as connection:
        for field in ("poverty_universe", "population_below_poverty", "households_total", "households_no_vehicle"):
            connection.execute(f"ALTER TABLE tracts ADD COLUMN {field} REAL")
        connection.execute("UPDATE tracts SET poverty_universe = 777")
        connection.execute("INSERT INTO metadata VALUES ('acs_vintage', '2023')")
    try:
        enrich_database(path, [make_evidence()], 2024)
    except ValueError as error:
        assert "tract sets do not match" in str(error)
    else:
        raise AssertionError("an incomplete ACS snapshot must fail closed")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT DISTINCT poverty_universe FROM tracts").fetchone()[0] == 777
        assert dict(connection.execute("SELECT key, value FROM metadata"))["acs_vintage"] == "2023"


def test_geography_vintage_mismatch_fails_before_join(tmp_path):
    path = tmp_path / "atlas.db"
    make_database(path, vintage="2010")
    try:
        enrich_database(path, [make_evidence(geography_vintage="2020")], 2024)
    except ValueError as error:
        assert "geography mismatch" in str(error)
    else:
        raise AssertionError("incompatible tract vintages must not be joined")
