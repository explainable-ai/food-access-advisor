from datetime import datetime, timezone

from tools.evidence_snapshots import read_changes, record_snapshot


NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)


def test_snapshot_detects_add_remove_modify_and_affected_tract(tmp_path):
    db = tmp_path / "snapshots.db"
    tracts = [{"tract_fips": "17031010100", "centroid_lat": 41.8, "centroid_lon": -87.6}]
    record_snapshot("licenses", [{"entity_id": "a", "name": "A", "lat": 41.8, "lon": -87.6, "status": "open"},
        {"entity_id": "b", "name": "B", "lat": 42.8, "lon": -87.6}], scope="urban", captured_at=NOW, db_path=db)
    result = record_snapshot("licenses", [{"entity_id": "a", "name": "A", "lat": 41.8, "lon": -87.6, "status": "closed"},
        {"entity_id": "c", "name": "C", "lat": 41.801, "lon": -87.601}], scope="urban",
        captured_at=NOW, affected_tracts=tracts, radius_miles=1, db_path=db)
    assert {change["change_type"] for change in result["changes"]} == {"added", "removed", "modified"}
    modified = next(change for change in result["changes"] if change["change_type"] == "modified")
    assert modified["affected_tracts"] == ["17031010100"]


def test_failed_fetch_is_unavailable_not_mass_removal(tmp_path):
    db = tmp_path / "snapshots.db"
    record_snapshot("osm", [{"entity_id": "a", "name": "A"}], scope="urban", captured_at=NOW, db_path=db)
    failed = record_snapshot("osm", [], scope="urban", status="failed", error="timeout", captured_at=NOW, db_path=db)
    assert [change["change_type"] for change in failed["changes"]] == ["unavailable"]
    assert failed["checksum"] is None


def test_stale_source_is_explicit_and_change_feed_is_filterable(tmp_path):
    db = tmp_path / "snapshots.db"
    result = record_snapshot("markets", [], scope="urban", status="stale", captured_at=NOW, db_path=db)
    assert result["changes"][0]["change_type"] == "stale"
    assert read_changes(source_id="other", db_path=db) == []
    assert read_changes(source_id="markets", db_path=db)[0]["after"] == {"record_count": 0}


def test_canonicalization_keeps_citation_but_ignores_retrieval_time(tmp_path):
    db = tmp_path / "snapshots.db"
    first = {"entity_id": "a", "name": "A", "source_citation": {
        "dataset_id": "official-1", "official_url": "https://example.org", "vintage": "2026",
        "retrieved_at": "2026-08-01T00:00:00Z"}}
    second = {**first, "source_citation": {**first["source_citation"], "retrieved_at": "2026-08-29T00:00:00Z"}}
    record_snapshot("source", [first], scope="urban", captured_at=NOW, db_path=db)
    unchanged = record_snapshot("source", [second], scope="urban", captured_at=NOW, db_path=db)
    assert unchanged["changes"] == []
    changed = {**second, "source_citation": {**second["source_citation"], "vintage": "2027"}}
    result = record_snapshot("source", [changed], scope="urban", captured_at=NOW, db_path=db)
    assert result["changes"][0]["change_type"] == "modified"
    assert result["changes"][0]["after"]["source_citation"]["dataset_id"] == "official-1"


def test_stable_osm_id_turns_rename_into_modification(tmp_path):
    db = tmp_path / "snapshots.db"
    record_snapshot("osm", [{"entity_id": "osm:node/7", "name": "Old", "lat": 41.8, "lon": -87.6}],
                    scope="urban", captured_at=NOW, db_path=db)
    result = record_snapshot("osm", [{"entity_id": "osm:node/7", "name": "New", "lat": 41.81, "lon": -87.61}],
                             scope="urban", captured_at=NOW, db_path=db)
    assert [change["change_type"] for change in result["changes"]] == ["modified"]
