"""S3-backed inventory storage for LastMile Market.

The current object is the source of truth and every write also creates an
immutable timestamped copy under ``inventory/versions``. Missing objects mean
the hub has not entered inventory yet; AWS/configuration failures are surfaced
as errors and are never converted into an empty inventory.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError


ON_HAND_KEY = "inventory/on-hand.json"
COLD_CHAIN_KEY = "inventory/cold-chain.json"


class InventoryStoreError(RuntimeError):
    """Inventory could not be read or written reliably."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class S3InventoryStore:
    def __init__(self, *, bucket: str | None = None, client=None, prefix: str | None = None):
        self.bucket = bucket or os.getenv("INVENTORY_BUCKET") or os.getenv("EVIDENCE_BUCKET")
        if not self.bucket:
            raise InventoryStoreError("INVENTORY_BUCKET or EVIDENCE_BUCKET must be configured")
        self.client = client or boto3.client("s3")
        self.prefix = (prefix if prefix is not None else os.getenv("INVENTORY_PREFIX", "")).strip("/")

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def read(self, key: str) -> list[dict[str, Any]]:
        object_key = self._key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=object_key)
            payload = json.loads(response["Body"].read())
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404", "NotFound"}:
                return []
            raise InventoryStoreError(f"Unable to read s3://{self.bucket}/{object_key}: {exc}") from exc
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InventoryStoreError(f"Inventory object s3://{self.bucket}/{object_key} is invalid: {exc}") from exc
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise InventoryStoreError(f"Inventory object s3://{self.bucket}/{object_key} must contain a JSON list")
        return payload

    def write(self, key: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise InventoryStoreError("Inventory writes require a list of objects")
        encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        current_key = self._key(key)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        filename = key.rsplit("/", 1)[-1]
        version_key = self._key(f"inventory/versions/{timestamp}-{uuid4().hex[:8]}-{filename}")
        kwargs = {
            "Bucket": self.bucket,
            "Body": encoded,
            "ContentType": "application/json",
            "ServerSideEncryption": "AES256",
        }
        try:
            self.client.put_object(Key=version_key, **kwargs)
            self.client.put_object(Key=current_key, **kwargs)
        except ClientError as exc:
            raise InventoryStoreError(f"Unable to write inventory in s3://{self.bucket}: {exc}") from exc
        return rows

    def upsert(self, key: str, row: dict[str, Any], identity: str) -> dict[str, Any]:
        item = dict(row)
        item[identity] = str(item.get(identity) or uuid4())
        item["last_updated"] = _utc_now()
        rows = self.read(key)
        index = next((i for i, existing in enumerate(rows) if str(existing.get(identity)) == item[identity]), None)
        if index is None:
            rows.append(item)
        else:
            rows[index] = {**rows[index], **item}
        self.write(key, rows)
        return item

    def on_hand(self) -> list[dict[str, Any]]:
        return self.read(ON_HAND_KEY)

    def cold_chain(self) -> list[dict[str, Any]]:
        return self.read(COLD_CHAIN_KEY)

    def upsert_on_hand(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.upsert(ON_HAND_KEY, row, "item_id")

    def upsert_cold_chain(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.upsert(COLD_CHAIN_KEY, row, "lot_id")
