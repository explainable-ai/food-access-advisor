from datetime import datetime, timedelta, timezone

from tools.evidence_snapshots import read_change_page, read_changes, record_snapshot, review_change


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


def test_change_page_groups_repeated_source_health_and_paginates(tmp_path):
    db = tmp_path / "snapshots.db"
    record_snapshot("markets", [], scope="urban", status="stale", captured_at=NOW, db_path=db)
    record_snapshot(
        "markets", [], scope="urban", status="stale",
        captured_at=NOW + timedelta(minutes=5), db_path=db,
    )
    record_snapshot(
        "licenses", [], scope="urban", status="failed", error="timeout",
        captured_at=NOW + timedelta(minutes=10), db_path=db,
    )

    first = read_change_page(limit=1, db_path=db)
    assert first["page_size"] == 1
    assert first["open_finding_count"] == 2
    assert first["source_count"] == 2
    assert first["next_cursor"]

    second = read_change_page(limit=1, cursor=first["next_cursor"], db_path=db)
    assert second["page_size"] == 1
    markets = second["items"][0]
    assert markets["source_id"] == "markets"
    assert markets["occurrence_count"] == 2


def test_acknowledging_latest_source_health_finding_closes_group(tmp_path):
    db = tmp_path / "snapshots.db"
    record_snapshot("markets", [], scope="urban", status="stale", captured_at=NOW, db_path=db)
    record_snapshot(
        "markets", [], scope="urban", status="stale",
        captured_at=NOW + timedelta(minutes=5), db_path=db,
    )
    finding = read_change_page(db_path=db)["items"][0]

    reviewed = review_change(
        source_scope=finding["source_scope"], record_key=finding["record_key"],
        action="acknowledged", reviewed_by="reviewer@example.org", db_path=db,
    )

    assert reviewed["review_status"] == "acknowledged"
    assert read_change_page(db_path=db)["open_finding_count"] == 0
    all_findings = read_change_page(status="all", db_path=db)
    assert all_findings["items"][0]["occurrence_count"] == 2


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


def test_completeness_guard_suppresses_mass_removals_and_preserves_complete_baseline(tmp_path):
    db = tmp_path / "snapshots.db"
    baseline = [{"entity_id": str(index)} for index in range(20)]
    record_snapshot("osm_resources", baseline, scope="urban", captured_at=NOW, db_path=db)

    partial = record_snapshot(
        "osm_resources", baseline[:10], scope="urban", captured_at=NOW, db_path=db,
        min_retained_fraction=0.75, min_baseline_records=10,
    )
    assert partial["status"] == "partial"
    assert partial["baseline_record_count"] == 20
    assert partial["retained_fraction"] == 0.5
    assert [change["change_type"] for change in partial["changes"]] == ["partial"]

    still_partial = record_snapshot(
        "osm_resources", baseline[:12], scope="urban", captured_at=NOW, db_path=db,
        min_retained_fraction=0.75, min_baseline_records=10,
    )
    assert still_partial["status"] == "partial"
    assert still_partial["baseline_record_count"] == 20

    recovered = record_snapshot(
        "osm_resources", baseline[:16], scope="urban", captured_at=NOW, db_path=db,
        min_retained_fraction=0.75, min_baseline_records=10,
    )
    assert recovered["status"] == "complete"
    assert [change["change_type"] for change in recovered["changes"]] == ["removed"] * 4


def test_explicit_partial_snapshot_never_emits_entity_changes(tmp_path):
    db = tmp_path / "snapshots.db"
    record_snapshot("source", [{"entity_id": "a"}], scope="urban", captured_at=NOW, db_path=db)
    result = record_snapshot(
        "source", [], scope="urban", status="partial", error="page incomplete",
        captured_at=NOW, db_path=db,
    )
    assert [change["change_type"] for change in result["changes"]] == ["partial"]
    assert result["changes"][0]["after"]["error"] == "page incomplete"


def test_completeness_guard_uses_entity_overlap_not_response_size(tmp_path):
    db = tmp_path / "snapshots.db"
    baseline = [{"entity_id": f"old-{index}"} for index in range(20)]
    record_snapshot("osm_resources", baseline, scope="urban", captured_at=NOW, db_path=db)

    unrelated = [{"entity_id": f"new-{index}"} for index in range(18)]
    result = record_snapshot(
        "osm_resources", unrelated, scope="urban", captured_at=NOW, db_path=db,
        min_retained_fraction=0.75, min_baseline_records=10,
    )

    assert result["status"] == "partial"
    assert result["baseline_entity_count"] == 20
    assert result["current_record_count"] == 18
    assert result["retained_entity_count"] == 0
    assert result["new_entity_count"] == 18
    assert result["retained_fraction"] == 0
    assert [change["change_type"] for change in result["changes"]] == ["partial"]
