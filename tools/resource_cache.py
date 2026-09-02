"""Prepared existing-resource cache for API reads and scheduled refreshes."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import boto3

from config import PILOT_CITY, PILOT_RURAL_COUNTY
from tools.existing_resources import get_existing_resources, get_rural_existing_resources

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "resource_cache"
DEFAULT_PREFIX = "resource-cache"


class ResourceCacheError(RuntimeError):
    """The prepared resource snapshot is unavailable or malformed."""


def _validate(resources):
    if not isinstance(resources, list):
        raise ResourceCacheError("resource cache does not contain a resource list")
    out = []
    for index, row in enumerate(resources):
        if not isinstance(row, dict):
            raise ResourceCacheError(f"resource cache row {index} is not an object")
        try:
            out.append({
                "kind": str(row["kind"]),
                "name": str(row.get("name") or "(unnamed)"),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                **({"entity_id": str(row["entity_id"])} if row.get("entity_id") else {}),
            })
        except (KeyError, TypeError, ValueError) as exc:
            raise ResourceCacheError(f"resource cache row {index} is invalid: {exc}") from exc
    return out


def _bucket():
    return os.getenv("RESOURCE_CACHE_BUCKET") or os.getenv("EVIDENCE_BUCKET")


def _key(scope):
    prefix = os.getenv("RESOURCE_CACHE_PREFIX", DEFAULT_PREFIX).strip("/")
    return f"{prefix}/{scope}.json"


def _required_coverage_bboxes(scope):
    config = PILOT_CITY if scope == "urban" else PILOT_RURAL_COUNTY
    areas = config.get("resource_areas")
    return [tuple(area["bbox"]) for area in areas] if areas else [tuple(config["bbox"])]


def _normalize_coverage_bboxes(payload):
    raw_values = payload.get("coverage_bboxes")
    if raw_values is None and payload.get("coverage_bbox") is not None:
        raw_values = [payload["coverage_bbox"]]
    if not isinstance(raw_values, (list, tuple)) or not raw_values:
        raise ResourceCacheError("resource cache has no valid coverage metadata")
    normalized = []
    for raw in raw_values:
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            raise ResourceCacheError("resource cache contains an invalid coverage box")
        try:
            normalized.append(tuple(float(value) for value in raw))
        except (TypeError, ValueError) as exc:
            raise ResourceCacheError("resource cache coverage contains a non-numeric value") from exc
    return normalized


def _covers(actual, expected):
    return (
        actual[0] <= expected[0]
        and actual[1] <= expected[1]
        and actual[2] >= expected[2]
        and actual[3] >= expected[3]
    )


def _validate_coverage(scope, payload):
    if not isinstance(payload, dict):
        raise ResourceCacheError("complete resource cache must include coverage metadata")
    actual_values = _normalize_coverage_bboxes(payload)
    missing = [
        expected
        for expected in _required_coverage_bboxes(scope)
        if not any(_covers(actual, expected) for actual in actual_values)
    ]
    if missing:
        raise ResourceCacheError(
            f"resource cache does not cover required {scope} bounds {missing}; "
            "refresh the prepared snapshot"
        )


def load_resource_cache(scope, require_complete_coverage=False):
    """Read a prepared snapshot; never performs a live Overpass call."""
    if scope not in {"urban", "rural"}:
        raise ValueError("resource cache scope must be urban or rural")
    bucket = _bucket()
    try:
        if bucket:
            body = boto3.client("s3").get_object(Bucket=bucket, Key=_key(scope))["Body"].read()
            payload = json.loads(body)
        else:
            path = Path(os.getenv("RESOURCE_CACHE_DIR", CACHE_DIR)) / f"{scope}.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        location = f"s3://{bucket}/{_key(scope)}" if bucket else str(Path(os.getenv("RESOURCE_CACHE_DIR", CACHE_DIR)) / f"{scope}.json")
        raise ResourceCacheError(f"prepared {scope} resource cache is unavailable at {location}: {exc}") from exc
    if require_complete_coverage:
        _validate_coverage(scope, payload)
    resources = payload.get("resources") if isinstance(payload, dict) else payload
    validated = _validate(resources)
    if require_complete_coverage and not validated:
        raise ResourceCacheError(
            f"prepared {scope} resource cache is empty; publish a verified county-wide snapshot"
        )
    return validated


def refresh_resource_cache(scope):
    """Fetch Overpass once and publish a snapshot for API readers."""
    if scope not in {"urban", "rural"}:
        raise ValueError("resource cache scope must be urban or rural")
    fetcher = get_existing_resources if scope == "urban" else get_rural_existing_resources
    resources = _validate(fetcher())
    coverage_bboxes = [list(values) for values in _required_coverage_bboxes(scope)]
    payload = {
        "scope": scope,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "coverage_bboxes": coverage_bboxes,
        "resources": resources,
    }
    encoded = json.dumps(payload, separators=(",", ":")).encode()
    bucket = _bucket()
    if bucket:
        boto3.client("s3").put_object(Bucket=bucket, Key=_key(scope), Body=encoded, ContentType="application/json")
    else:
        path = Path(os.getenv("RESOURCE_CACHE_DIR", CACHE_DIR)) / f"{scope}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(encoded)
        temporary.replace(path)
    return payload


def refresh_all_resource_caches():
    return {scope: refresh_resource_cache(scope) for scope in ("urban", "rural")}
