"""Deterministic execution path for one Watchdog recheck pass.

The Watchdog's comparisons, status decisions, snapshots, and database writes
do not require language-model reasoning. Keeping those operations here makes
the run auditable and prevents raw resource inventories from accumulating in
an agent conversation. Bedrock receives only the compact result of this pass.
"""

from collections.abc import Callable
from typing import Any

from tools.evidence_snapshots import record_resource_snapshot
from tools.existing_resources import get_existing_resources, get_rural_existing_resources
from tools.flagged_tracts import read_flagged_tracts, update_flagged_tract
from tools.recheck_status import (
    NEARBY_THRESHOLD_MILES,
    RURAL_NEARBY_THRESHOLD_MILES,
    check_resource_appeared,
)

MAX_RETURNED_RESULTS = 100
MAX_RETURNED_ERRORS = 50


def _short_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def _empty_result(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "pending": 0,
        "checked": 0,
        "possible_change": 0,
        "still_needed": 0,
        "by_type": {},
        "source_health": [],
        "result_count": 0,
        "results": [],
        "results_truncated": False,
        "error_count": 0,
        "errors": [],
        "errors_truncated": False,
    }


def _snapshot_health(snapshot: dict, scope: str, fallback_count: int) -> dict:
    changes = snapshot.get("changes") or []
    return {
        "source_id": "osm_resources",
        "scope": scope,
        "status": snapshot.get("status", "complete"),
        "record_count": snapshot.get("record_count", fallback_count),
        "change_count": len(changes),
    }


def _note_for_check(check: dict, threshold: float) -> str:
    kind = check.get("nearest_kind")
    distance = check.get("nearest_distance_miles")
    if check.get("resource_now_nearby"):
        return (
            f"OSM reports {kind or 'a food resource'} {distance} miles from the tract centroid; "
            "human verification required."
        )
    if kind is None or distance is None:
        return f"No OSM food resource found within the {threshold:g}-mile threshold."
    return (
        f"Nearest OSM {kind} is {distance} miles away, outside the "
        f"{threshold:g}-mile threshold."
    )


def run_watchdog_pass(
    *,
    read_fn: Callable | None = None,
    urban_fetch_fn: Callable | None = None,
    rural_fetch_fn: Callable | None = None,
    snapshot_fn: Callable | None = None,
    check_fn: Callable | None = None,
    update_fn: Callable | None = None,
) -> dict[str, Any]:
    """Run one complete recheck pass without an LLM or agent tool loop.

    Dependencies are injectable so the state transitions can be tested with
    no AWS, Overpass, or SQLite access.
    """
    read_fn = read_fn or read_flagged_tracts
    urban_fetch_fn = urban_fetch_fn or get_existing_resources
    rural_fetch_fn = rural_fetch_fn or get_rural_existing_resources
    snapshot_fn = snapshot_fn or record_resource_snapshot
    check_fn = check_fn or check_resource_appeared
    update_fn = update_fn or update_flagged_tract

    try:
        pending = read_fn(status="pending")
        if not isinstance(pending, list):
            raise TypeError("read_flagged_tracts must return a list")
    except Exception as exc:
        result = _empty_result("partial")
        result["error_count"] = 1
        result["errors"] = [{"stage": "read_backlog", "error": _short_error(exc)}]
        return result

    if not pending:
        return _empty_result("no_pending")

    source_specs = {
        "site": ("urban", urban_fetch_fn, NEARBY_THRESHOLD_MILES),
        "route": ("rural", rural_fetch_fn, RURAL_NEARBY_THRESHOLD_MILES),
    }
    resources_by_type: dict[str, list] = {}
    source_health = []
    errors = []

    for recommendation_type in ("site", "route"):
        if not any(row.get("recommendation_type") == recommendation_type for row in pending):
            continue
        scope, fetch_fn, _threshold = source_specs[recommendation_type]
        try:
            resources = fetch_fn()
            if not isinstance(resources, list):
                raise TypeError(f"{scope} resource fetch must return a list")
            snapshot = snapshot_fn(
                source_id="osm_resources",
                scope=scope,
                resources=resources,
                status="complete",
            )
            resources_by_type[recommendation_type] = resources
            source_health.append(_snapshot_health(snapshot, scope, len(resources)))
        except Exception as exc:
            error = _short_error(exc)
            try:
                snapshot_fn(
                    source_id="osm_resources",
                    scope=scope,
                    resources=[],
                    status="failed",
                    error=error,
                )
            except Exception as snapshot_exc:
                error = f"{error}; snapshot failure: {_short_error(snapshot_exc)}"[:600]
            source_health.append({
                "source_id": "osm_resources",
                "scope": scope,
                "status": "failed",
                "record_count": 0,
                "change_count": 0,
                "error": error,
            })
            errors.append({"stage": "fetch_or_snapshot", "scope": scope, "error": error})

    counts = {
        "checked": 0,
        "possible_change": 0,
        "still_needed": 0,
    }
    by_type: dict[str, dict[str, int]] = {}
    row_results = []

    for row in pending:
        recommendation_type = row.get("recommendation_type")
        tract_fips = str(row.get("tract_fips", ""))
        if recommendation_type not in source_specs:
            errors.append({
                "stage": "validate_row",
                "tract_fips": tract_fips,
                "error": f"Unsupported recommendation_type: {recommendation_type!r}",
            })
            continue
        if recommendation_type not in resources_by_type:
            continue
        centroid_lat = row.get("centroid_lat")
        centroid_lon = row.get("centroid_lon")
        if centroid_lat is None or centroid_lon is None:
            errors.append({
                "stage": "validate_row",
                "tract_fips": tract_fips,
                "recommendation_type": recommendation_type,
                "error": "Missing tract centroid; row left pending.",
            })
            continue

        _scope, _fetch_fn, threshold = source_specs[recommendation_type]
        try:
            check = check_fn(
                centroid_lat=centroid_lat,
                centroid_lon=centroid_lon,
                resources=resources_by_type[recommendation_type],
                threshold_miles=threshold,
            )
            status = "possible_change" if check.get("resource_now_nearby") else "still_needed"
            update = update_fn(
                tract_fips=tract_fips,
                recommendation_type=recommendation_type,
                status=status,
                note=_note_for_check(check, threshold),
            )
            if isinstance(update, dict) and update.get("error"):
                raise RuntimeError(update["error"])
        except Exception as exc:
            errors.append({
                "stage": "check_or_update",
                "tract_fips": tract_fips,
                "recommendation_type": recommendation_type,
                "error": _short_error(exc),
            })
            continue

        type_counts = by_type.setdefault(
            recommendation_type,
            {"checked": 0, "possible_change": 0, "still_needed": 0},
        )
        counts["checked"] += 1
        counts[status] += 1
        type_counts["checked"] += 1
        type_counts[status] += 1
        row_results.append({
            "tract_fips": tract_fips,
            "recommendation_type": recommendation_type,
            "status": status,
            "nearest_kind": check.get("nearest_kind"),
            "nearest_distance_miles": check.get("nearest_distance_miles"),
        })

    return {
        "status": "partial" if errors else "complete",
        "pending": len(pending),
        **counts,
        "by_type": by_type,
        "source_health": source_health,
        "result_count": len(row_results),
        "results": row_results[:MAX_RETURNED_RESULTS],
        "results_truncated": len(row_results) > MAX_RETURNED_RESULTS,
        "error_count": len(errors),
        "errors": errors[:MAX_RETURNED_ERRORS],
        "errors_truncated": len(errors) > MAX_RETURNED_ERRORS,
    }
