"""Prepared existing-resource cache for API reads and scheduled refreshes."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import boto3

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


def load_resource_cache(scope):
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
    resources = payload.get("resources") if isinstance(payload, dict) else payload
    return _validate(resources)


def refresh_resource_cache(scope):
    """Fetch Overpass once and publish a snapshot for API readers."""
    if scope not in {"urban", "rural"}:
        raise ValueError("resource cache scope must be urban or rural")
    fetcher = get_existing_resources if scope == "urban" else get_rural_existing_resources
    resources = _validate(fetcher())
    payload = {"scope": scope, "refreshed_at": datetime.now(timezone.utc).isoformat(), "resources": resources}
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
