"""Durable evidence snapshots and deterministic Watchdog change detection."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from strands import tool
except ImportError:  # pragma: no cover
    def tool(func=None, **_kwargs):
        return (lambda f: f) if func is None else func

from tools.geo import haversine_miles
from storage.aws_persistence import AwsEvidenceStore, aws_storage_enabled

DB_PATH = Path(__file__).parent.parent / "data" / "evidence_snapshots.db"
SUCCESS_STATUSES = {"complete", "partial", "stale"}
OSM_MIN_RETAINED_FRACTION = 0.75
OSM_MIN_BASELINE_RECORDS = 10


def _connect(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS evidence_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
            scope TEXT NOT NULL, captured_at TEXT NOT NULL, status TEXT NOT NULL,
            checksum TEXT, records_json TEXT NOT NULL, error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_source_scope
            ON evidence_snapshots(source_id, scope, id DESC);
        CREATE TABLE IF NOT EXISTS evidence_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id INTEGER NOT NULL,
            previous_snapshot_id INTEGER, source_id TEXT NOT NULL, scope TEXT NOT NULL,
            detected_at TEXT NOT NULL, change_type TEXT NOT NULL, entity_id TEXT,
            before_json TEXT, after_json TEXT,
            affected_tracts_json TEXT NOT NULL DEFAULT '[]'
        );
    """)
    connection.commit()
    return connection


def _without_retrieval_times(value: Any) -> Any:
    """Remove volatile retrieval timestamps without discarding provenance."""
    if isinstance(value, dict):
        return {key: _without_retrieval_times(value[key]) for key in sorted(value) if key != "retrieved_at"}
    if isinstance(value, list):
        return [_without_retrieval_times(item) for item in value]
    return value


def _canonical_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [_without_retrieval_times(row) for row in records]
    return sorted(normalized, key=_entity_id)


def _entity_id(record: dict[str, Any]) -> str:
    value = record.get("entity_id") or record.get("id")
    if value is None:
        value = f"{record.get('kind', 'resource')}:{record.get('name', '')}:{record.get('lat')}:{record.get('lon')}"
    return str(value)


def _nearby_tracts(record: dict[str, Any] | None, tracts: list[dict[str, Any]], radius_miles: float) -> list[str]:
    if not record or record.get("lat") is None or record.get("lon") is None:
        return []
    affected = []
    for tract in tracts:
        if tract.get("centroid_lat") is None or tract.get("centroid_lon") is None:
            continue
        distance = haversine_miles(float(record["lat"]), float(record["lon"]),
            float(tract["centroid_lat"]), float(tract["centroid_lon"]))
        if distance <= radius_miles:
            affected.append(str(tract["tract_fips"]))
    return sorted(set(affected))


def _build_changes(status: str, canonical: list[dict[str, Any]], previous_records: list[dict[str, Any]] | None,
                   tracts: list[dict[str, Any]], radius_miles: float, error: str | None) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if status == "failed":
        changes.append({"change_type": "unavailable", "entity_id": None, "before": None,
                        "after": {"error": error or "source unavailable"}, "affected_tracts": []})
    elif status == "partial":
        changes.append({"change_type": "partial", "entity_id": None, "before": None,
                        "after": {"record_count": len(canonical), "error": error}, "affected_tracts": []})
    elif status == "stale":
        changes.append({"change_type": "stale", "entity_id": None, "before": None,
                        "after": {"record_count": len(canonical)}, "affected_tracts": []})
    if status != "complete" or previous_records is None:
        return changes
    old = {_entity_id(row): row for row in previous_records}
    new = {_entity_id(row): row for row in canonical}
    for entity_id in sorted(new.keys() - old.keys()):
        changes.append({"change_type": "added", "entity_id": entity_id, "before": None,
            "after": new[entity_id], "affected_tracts": _nearby_tracts(new[entity_id], tracts, radius_miles)})
    for entity_id in sorted(old.keys() - new.keys()):
        changes.append({"change_type": "removed", "entity_id": entity_id, "before": old[entity_id],
            "after": None, "affected_tracts": _nearby_tracts(old[entity_id], tracts, radius_miles)})
    for entity_id in sorted(old.keys() & new.keys()):
        if old[entity_id] != new[entity_id]:
            nearby = set(_nearby_tracts(old[entity_id], tracts, radius_miles))
            nearby.update(_nearby_tracts(new[entity_id], tracts, radius_miles))
            changes.append({"change_type": "modified", "entity_id": entity_id, "before": old[entity_id],
                            "after": new[entity_id], "affected_tracts": sorted(nearby)})
    return changes


def _apply_completeness_guard(
    status: str,
    current_records: list[dict[str, Any]],
    previous_records: list[dict[str, Any]] | None,
    error: str | None,
    min_retained_fraction: float | None,
    min_baseline_records: int,
) -> tuple[str, str | None, dict[str, Any]]:
    """Downgrade an implausibly small successful response to partial.

    The last complete snapshot remains the baseline, so repeated incomplete
    responses cannot ratchet the baseline downward and later appear healthy.
    """
    if min_retained_fraction is None:
        return status, error, {}
    if not 0 < min_retained_fraction <= 1:
        raise ValueError("min_retained_fraction must be between 0 and 1")
    if min_baseline_records < 1:
        raise ValueError("min_baseline_records must be positive")
    if status != "complete" or previous_records is None:
        return status, error, {}
    baseline_count = len(previous_records)
    if baseline_count < min_baseline_records:
        return status, error, {}
    baseline_ids = {_entity_id(record) for record in previous_records}
    current_ids = {_entity_id(record) for record in current_records}
    retained_count = len(baseline_ids & current_ids)
    retained_fraction = retained_count / len(baseline_ids)
    if retained_fraction >= min_retained_fraction:
        return status, error, {}
    guard_error = (
        f"Completeness guard: retained {retained_count}/{len(baseline_ids)} baseline entities "
        f"({retained_fraction:.1%}), below the {min_retained_fraction:.0%} threshold."
    )
    if error:
        guard_error = f"{error}; {guard_error}"
    return "partial", guard_error, {
        "baseline_record_count": baseline_count,
        "baseline_entity_count": len(baseline_ids),
        "current_record_count": len(current_records),
        "retained_entity_count": retained_count,
        "new_entity_count": len(current_ids - baseline_ids),
        "retained_fraction": round(retained_fraction, 4),
        "minimum_retained_fraction": min_retained_fraction,
    }


def record_snapshot(source_id: str, records: list[dict[str, Any]], *, scope: str,
                    status: str = "complete", error: str | None = None,
                    captured_at: datetime | None = None, affected_tracts: list[dict[str, Any]] | None = None,
                    radius_miles: float = 10.0, db_path: Path = DB_PATH,
                    min_retained_fraction: float | None = None,
                    min_baseline_records: int = 1) -> dict[str, Any]:
    """Persist a source observation and compare it to the last successful one.

    A failed fetch emits ``unavailable`` and never emits removals. Only a
    successful empty result can mean every previously observed entity disappeared.
    """
    if status not in SUCCESS_STATUSES | {"failed"}:
        raise ValueError("status must be complete, partial, stale, or failed")
    if not source_id or not scope:
        raise ValueError("source_id and scope are required")
    captured = captured_at or datetime.now(timezone.utc)
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError("captured_at must be timezone-aware")
    canonical = _canonical_records(records) if status in SUCCESS_STATUSES else []
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(payload.encode()).hexdigest() if status in SUCCESS_STATUSES else None
    tracts = affected_tracts or []
    if aws_storage_enabled() and db_path == DB_PATH:
        store = AwsEvidenceStore()
        previous = store.load_previous_success(source_id, scope)
        previous_id = previous["snapshot_id"] if previous else None
        status, error, guard = _apply_completeness_guard(
            status, canonical, previous["records"] if previous else None,
            error, min_retained_fraction, min_baseline_records,
        )
        changes = _build_changes(status, canonical, previous["records"] if previous else None,
                                 tracts, radius_miles, error)
        snapshot_id = store.save_snapshot(source_id=source_id, scope=scope,
            captured_at=captured.isoformat(), status=status, checksum=checksum, payload=payload,
            error=error, previous_snapshot_id=previous_id)
        store.save_changes(snapshot_id=snapshot_id, previous_snapshot_id=previous_id,
            source_id=source_id, scope=scope, detected_at=captured.isoformat(), changes=changes)
        return {"snapshot_id": snapshot_id, "source_id": source_id, "scope": scope, "status": status,
                "record_count": len(canonical), "checksum": checksum, "changes": changes,
                "error": error, **guard}

    connection = _connect(db_path)
    try:
        previous = connection.execute(
            "SELECT * FROM evidence_snapshots WHERE source_id=? AND scope=? AND status='complete' ORDER BY id DESC LIMIT 1",
            (source_id, scope),
        ).fetchone()
        previous_records = json.loads(previous["records_json"]) if previous is not None else None
        status, error, guard = _apply_completeness_guard(
            status, canonical, previous_records, error,
            min_retained_fraction, min_baseline_records,
        )
        cursor = connection.execute(
            "INSERT INTO evidence_snapshots(source_id,scope,captured_at,status,checksum,records_json,error) VALUES(?,?,?,?,?,?,?)",
            (source_id, scope, captured.isoformat(), status, checksum, payload, error),
        )
        snapshot_id = cursor.lastrowid
        changes = _build_changes(status, canonical, previous_records, tracts, radius_miles, error)
        for change in changes:
            connection.execute(
                "INSERT INTO evidence_changes(snapshot_id,previous_snapshot_id,source_id,scope,detected_at,change_type,entity_id,before_json,after_json,affected_tracts_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (snapshot_id, previous["id"] if previous else None, source_id, scope, captured.isoformat(),
                 change["change_type"], change["entity_id"], json.dumps(change["before"], sort_keys=True),
                 json.dumps(change["after"], sort_keys=True), json.dumps(change["affected_tracts"])),
            )
        connection.commit()
        return {"snapshot_id": snapshot_id, "source_id": source_id, "scope": scope, "status": status,
                "record_count": len(canonical), "checksum": checksum, "changes": changes,
                "error": error, **guard}
    finally:
        connection.close()


@tool
def record_resource_snapshot(source_id: str, scope: str, resources: list,
                             status: str = "complete", error: str = "") -> dict:
    """Record a Watchdog source result and report resource/source-health changes."""
    return record_snapshot(
        source_id, resources, scope=scope, status=status, error=error or None,
        min_retained_fraction=OSM_MIN_RETAINED_FRACTION,
        min_baseline_records=OSM_MIN_BASELINE_RECORDS,
    )


def read_changes(*, limit: int = 100, source_id: str | None = None, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    if aws_storage_enabled() and db_path == DB_PATH:
        return AwsEvidenceStore().read_changes(limit=limit, source_id=source_id)
    if not db_path.exists():
        return []
    connection = _connect(db_path)
    try:
        query, params = "SELECT * FROM evidence_changes", []
        if source_id:
            query += " WHERE source_id=?"
            params.append(source_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = connection.execute(query, params).fetchall()
        return [{**dict(row), "before": json.loads(row["before_json"]), "after": json.loads(row["after_json"]),
                 "affected_tracts": json.loads(row["affected_tracts_json"])} for row in rows]
    finally:
        connection.close()
