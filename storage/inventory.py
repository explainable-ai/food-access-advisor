"""Versioned S3 inventory storage for LastMile Market operations."""

from __future__ import annotations

import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any, Iterable

import boto3


ON_HAND_KEY = "inventory/on-hand.json"
COLD_CHAIN_KEY = "inventory/cold-chain.json"

_CACHE_LOCK = Lock()
_READ_CACHE: dict[
    tuple[str, str], tuple[float, list[dict[str, Any]], str | None]
] = {}
_CACHE_GENERATIONS: dict[tuple[str, str], int] = {}
_CACHE_EPOCH = 0


class InventoryStoreError(RuntimeError):
    """Raised when inventory cannot be read or safely updated."""


class InventoryObjectNotFound(InventoryStoreError):
    """Raised only when S3 explicitly reports that an object is absent."""


class InventoryConflictError(InventoryStoreError):
    """Raised when an inventory object changed during a read/merge/write cycle."""


_UNCONDITIONAL = object()
_ALIAS_FAMILIES = (
    ("on_hand", "quantity", "qty"),
    ("unit_weight_lbs", "weight_lbs"),
    ("risk_status", "cold_chain_risk"),
)


def _identity(item: dict[str, Any]) -> str:
    for key in ("item_id", "sku", "item"):
        value = str(item.get(key, "")).strip()
        if value:
            return value
    raise ValueError("Each inventory item requires item_id, sku, or item")


def _validate_numeric_fields(item: dict[str, Any]) -> None:
    for key in ("on_hand", "quantity", "qty"):
        if key not in item or item[key] is None:
            continue
        if isinstance(item[key], bool):
            raise ValueError(f"Inventory field {key} must be a non-negative number")
        try:
            value = float(item[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Inventory field {key} must be a non-negative number") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Inventory field {key} must be a non-negative number")
    for key in ("unit_weight_lbs", "weight_lbs"):
        if key not in item or item[key] is None:
            continue
        if isinstance(item[key], bool):
            raise ValueError(f"Inventory field {key} must be a positive number")
        try:
            value = float(item[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Inventory field {key} must be a positive number") from exc
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Inventory field {key} must be a positive number")


def _merge_item(current: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current)
    for family in _ALIAS_FAMILIES:
        if any(key in update for key in family):
            for key in family:
                merged.pop(key, None)
    merged.update(update)
    return merged


def _cache_ttl_seconds() -> float:
    """Return the short read-through cache TTL; zero disables caching."""
    raw = os.getenv("INVENTORY_CACHE_TTL_SECONDS", "15")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 15.0


def clear_inventory_cache(bucket: str | None = None, key: str | None = None) -> None:
    """Clear cached inventory snapshots, optionally scoped to one object."""
    global _CACHE_EPOCH
    with _CACHE_LOCK:
        if bucket is None and key is None:
            _READ_CACHE.clear()
            _CACHE_GENERATIONS.clear()
            _CACHE_EPOCH += 1
            return
        candidates = set(_READ_CACHE) | set(_CACHE_GENERATIONS)
        if bucket is not None and key is not None:
            candidates.add((bucket, key))
        for cache_key in candidates:
            if (bucket is None or cache_key[0] == bucket) and (
                key is None or cache_key[1] == key
            ):
                _READ_CACHE.pop(cache_key, None)
                _CACHE_GENERATIONS[cache_key] = (
                    _CACHE_GENERATIONS.get(cache_key, 0) + 1
                )


class S3InventoryStore:
    """Read and update inventory directly in the configured evidence bucket."""

    def __init__(self, bucket: str | None = None, s3_client: Any | None = None):
        self.bucket = (
            bucket
            or os.getenv("INVENTORY_BUCKET")
            or os.getenv("RESOURCE_CACHE_BUCKET")
            or os.getenv("EVIDENCE_BUCKET")
        )
        if not self.bucket:
            raise InventoryStoreError(
                "Inventory bucket is not configured; set INVENTORY_BUCKET, RESOURCE_CACHE_BUCKET, or EVIDENCE_BUCKET"
            )
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        self.s3 = s3_client or boto3.client("s3", region_name=region)

    def read_with_etag(
        self, key: str, *, use_cache: bool = True
    ) -> tuple[list[dict[str, Any]], str | None]:
        cache_key = (self.bucket, key)
        ttl = _cache_ttl_seconds()
        cache_epoch = 0
        cache_generation = 0
        if use_cache and ttl > 0:
            now = monotonic()
            with _CACHE_LOCK:
                cache_epoch = _CACHE_EPOCH
                cache_generation = _CACHE_GENERATIONS.get(cache_key, 0)
                cached = _READ_CACHE.get(cache_key)
                if cached and cached[0] > now:
                    return deepcopy(cached[1]), cached[2]
                if cached:
                    _READ_CACHE.pop(cache_key, None)
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            payload = json.loads(response["Body"].read())
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in {"NoSuchKey", "404", "NotFound"}:
                raise InventoryObjectNotFound(f"Inventory object {key} does not exist") from exc
            raise InventoryStoreError(f"Could not read inventory object {key}") from exc
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise InventoryStoreError(f"Inventory object {key} must contain a JSON array of objects")
        etag = response.get("ETag")
        if use_cache and ttl > 0:
            with _CACHE_LOCK:
                if (
                    _CACHE_EPOCH == cache_epoch
                    and _CACHE_GENERATIONS.get(cache_key, 0) == cache_generation
                ):
                    _READ_CACHE[cache_key] = (
                        monotonic() + ttl,
                        deepcopy(payload),
                        etag,
                    )
        return payload, etag

    def read(self, key: str) -> list[dict[str, Any]]:
        return self.read_with_etag(key)[0]

    def read_many(self, keys: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
        """Read independent inventory objects concurrently on a cold cache."""
        keys = list(dict.fromkeys(keys))
        if not keys:
            return {}
        with ThreadPoolExecutor(max_workers=min(4, len(keys))) as executor:
            values = executor.map(self.read, keys)
            return dict(zip(keys, values))

    def write(
        self,
        key: str,
        data: list[dict[str, Any]],
        *,
        expected_etag: str | None | object = _UNCONDITIONAL,
    ) -> dict[str, str]:
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise ValueError("Inventory data must be a list of objects")
        for item in data:
            _identity(item)
            _validate_numeric_fields(item)
        body = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        version_key = f"inventory/versions/{timestamp}-{key.rsplit('/', 1)[-1]}"
        try:
            conditions = {}
            if expected_etag is not _UNCONDITIONAL:
                conditions = (
                    {"IfMatch": expected_etag}
                    if expected_etag is not None
                    else {"IfNoneMatch": "*"}
                )
            # Stage the immutable version before conditionally publishing the
            # new current object. A failed publish may leave an unused staged
            # version, but can never leave an unversioned current mutation.
            self.s3.put_object(Bucket=self.bucket, Key=version_key, Body=body, ContentType="application/json")
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
                **conditions,
            )
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in {"PreconditionFailed", "412", "ConditionalRequestConflict", "409"}:
                raise InventoryConflictError(
                    f"Inventory object {key} changed during update"
                ) from exc
            raise InventoryStoreError(f"Could not write inventory object {key}") from exc
        clear_inventory_cache(self.bucket, key)
        return {"key": key, "version_key": version_key}

    def merge(self, key: str, updates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        updates = list(updates)
        update_by_id = {_identity(item): item for item in updates}
        for attempt in range(3):
            try:
                # Merge must always compare against the latest ETag rather than
                # a short-lived read cache entry.
                current, etag = self.read_with_etag(key, use_cache=False)
            except InventoryObjectNotFound:
                current, etag = [], None
            merged = []
            seen = set()
            for item in current:
                item_id = _identity(item)
                merged.append(_merge_item(item, update_by_id.get(item_id, {})))
                seen.add(item_id)
            merged.extend(item for item_id, item in update_by_id.items() if item_id not in seen)
            try:
                self.write(key, merged, expected_etag=etag)
                return merged
            except InventoryConflictError:
                if attempt == 2:
                    raise
        raise InventoryConflictError(f"Inventory object {key} changed repeatedly during update")


def read_inventory(key: str, store: S3InventoryStore | None = None) -> list[dict[str, Any]]:
    return (store or S3InventoryStore()).read(key)


def write_inventory(key: str, data: list[dict[str, Any]], store: S3InventoryStore | None = None) -> dict[str, str]:
    return (store or S3InventoryStore()).write(key, data)


def get_inventory_store() -> S3InventoryStore:
    return S3InventoryStore()
