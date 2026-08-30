from tools.recheck_status import NEARBY_THRESHOLD_MILES, RURAL_NEARBY_THRESHOLD_MILES
from tools.watchdog_run import run_watchdog_pass


def test_pass_fetches_each_needed_region_once_and_uses_matching_threshold():
    fetches = []
    snapshots = []
    checks = []
    updates = []
    rows = [
        {"tract_fips": "17031000100", "recommendation_type": "site", "centroid_lat": 41.8, "centroid_lon": -87.6},
        {"tract_fips": "17003000100", "recommendation_type": "route", "centroid_lat": 37.1, "centroid_lon": -89.2},
    ]

    def fetch(scope):
        fetches.append(scope)
        return [{"entity_id": f"osm:node/{scope}", "kind": "grocery", "lat": 1.0, "lon": 2.0}]

    def snapshot(**kwargs):
        snapshots.append(kwargs)
        return {"status": kwargs["status"], "record_count": len(kwargs["resources"]), "changes": []}

    def check(**kwargs):
        checks.append(kwargs)
        return {
            "resource_now_nearby": kwargs["threshold_miles"] == RURAL_NEARBY_THRESHOLD_MILES,
            "nearest_kind": "grocery",
            "nearest_distance_miles": 7.0,
        }

    def update(**kwargs):
        updates.append(kwargs)
        return kwargs

    result = run_watchdog_pass(
        read_fn=lambda status: rows,
        urban_fetch_fn=lambda: fetch("urban"),
        rural_fetch_fn=lambda: fetch("rural"),
        snapshot_fn=snapshot,
        check_fn=check,
        update_fn=update,
    )

    assert fetches == ["urban", "rural"]
    assert [item["scope"] for item in snapshots] == ["urban", "rural"]
    assert [item["threshold_miles"] for item in checks] == [
        NEARBY_THRESHOLD_MILES,
        RURAL_NEARBY_THRESHOLD_MILES,
    ]
    assert [item["status"] for item in updates] == ["still_needed", "possible_change"]
    assert result["checked"] == 2
    assert result["by_type"]["site"]["still_needed"] == 1
    assert result["by_type"]["route"]["possible_change"] == 1


def test_failed_source_is_snapshotted_and_rows_remain_pending():
    snapshots = []
    updates = []

    def failed_fetch():
        raise TimeoutError("Overpass unavailable")

    def snapshot(**kwargs):
        snapshots.append(kwargs)
        return {"status": kwargs["status"], "changes": []}

    result = run_watchdog_pass(
        read_fn=lambda status: [{
            "tract_fips": "17031000100",
            "recommendation_type": "site",
            "centroid_lat": 41.8,
            "centroid_lon": -87.6,
        }],
        urban_fetch_fn=failed_fetch,
        snapshot_fn=snapshot,
        update_fn=lambda **kwargs: updates.append(kwargs),
    )

    assert snapshots[0]["status"] == "failed"
    assert snapshots[0]["resources"] == []
    assert updates == []
    assert result["status"] == "partial"
    assert result["checked"] == 0
    assert result["source_health"][0]["status"] == "failed"


def test_empty_backlog_does_not_fetch_or_call_bedrock_adjacent_work():
    def should_not_run():
        raise AssertionError("source fetch should not run")

    result = run_watchdog_pass(
        read_fn=lambda status: [],
        urban_fetch_fn=should_not_run,
        rural_fetch_fn=should_not_run,
    )

    assert result["status"] == "no_pending"
    assert result["checked"] == 0


def test_missing_centroid_leaves_row_pending_and_reports_partial_run():
    updates = []
    result = run_watchdog_pass(
        read_fn=lambda status: [{
            "tract_fips": "17031000100",
            "recommendation_type": "site",
            "centroid_lat": None,
            "centroid_lon": -87.6,
        }],
        urban_fetch_fn=lambda: [],
        snapshot_fn=lambda **kwargs: {"status": "complete", "record_count": 0, "changes": []},
        update_fn=lambda **kwargs: updates.append(kwargs),
    )

    assert updates == []
    assert result["status"] == "partial"
    assert result["errors"][0]["stage"] == "validate_row"
