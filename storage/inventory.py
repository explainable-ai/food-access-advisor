"""Versioned S3 inventory storage for LastMile Market operations."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Iterable

import boto3


ON_HAND_KEY = "inventory/on-hand.json"
COLD_CHAIN_KEY = "inventory/cold-chain.json"


class InventoryStoreError(RuntimeError):
    """Raised when inventory cannot be read or safely updated."""


class InventoryObjectNotFound(InventoryStoreError):
    """Raised only when S3 explicitly reports that an object is absent."""


class InventoryConflictError(InventoryStoreError):
    """Raised when an inventory object changed during a read/merge/write cycle."""


_UNCONDITIONAL = object()


def _identity(item: dict[str, Any]) -> str:
    for key in ("item_id", "sku", "item"):
        value = str(item.get(key, "")).strip()
        if value:
            return f"{key}:{value}"
    raise ValueError("Each inventory item requires item_id, sku, or item")


class S3InventoryStore:
    """Read and update inventory directly in the configured evidence bucket."""

    def __init__(self, bucket: str | None = None, s3_client: Any | None = None):
        self.bucket = bucket or os.getenv("INVENTORY_BUCKET") or os.getenv("RESOURCE_CACHE_BUCKET")
        if not self.bucket:
            raise InventoryStoreError(
                "Inventory bucket is not configured; set INVENTORY_BUCKET or RESOURCE_CACHE_BUCKET"
            )
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        self.s3 = s3_client or boto3.client("s3", region_name=region)

    def read_with_etag(self, key: str) -> tuple[list[dict[str, Any]], str | None]:
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
        return payload, response.get("ETag")

    def read(self, key: str) -> list[dict[str, Any]]:
        return self.read_with_etag(key)[0]

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
        return {"key": key, "version_key": version_key}

    def merge(self, key: str, updates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        updates = list(updates)
        update_by_id = {_identity(item): item for item in updates}
        for attempt in range(3):
            try:
                current, etag = self.read_with_etag(key)
            except InventoryObjectNotFound:
                current, etag = [], None
            merged = []
            seen = set()
            for item in current:
                item_id = _identity(item)
                merged.append({**item, **update_by_id.get(item_id, {})})
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
