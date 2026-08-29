import sqlite3
from datetime import datetime, timezone

from data.prep_acs import enrich_database
from data_sources.contracts import DataQualityReport, EvidenceStatus, EvidenceValue, SourceCitation, TractEvidence


def test_enrich_database_adds_real_acs_values_and_metadata(tmp_path):
    path = tmp_path / "atlas.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tracts (tract_fips TEXT PRIMARY KEY, population INTEGER, low_access_half_mile INTEGER, low_access_one_mile INTEGER, centroid_lat REAL, centroid_lon REAL)")
        connection.execute("INSERT INTO tracts VALUES ('17031010100', 1000, 1, 1, 41.0, -87.0)")
    citation = SourceCitation(source_name="Census", dataset_name="ACS", official_url="https://api.census.gov",
        vintage="2024", retrieved_at=datetime.now(timezone.utc), geographic_level="tract")
    values = {name: EvidenceValue(field=name, value=value) for name, value in {
        "poverty_universe": 900, "population_below_poverty": 225,
        "households_total": 400, "households_no_vehicle": 80}.items()}
    evidence = TractEvidence(tract_geoid="17031010100", geography_vintage="2020", source_citations=[citation],
        quality=DataQualityReport(status=EvidenceStatus.COMPLETE), values=values)
    assert enrich_database(path, [evidence], 2024) == 1
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT poverty_universe, population_below_poverty, households_total, households_no_vehicle FROM tracts").fetchone()
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    assert row == (900.0, 225.0, 400.0, 80.0)
    assert metadata["acs_vintage"] == "2024"
