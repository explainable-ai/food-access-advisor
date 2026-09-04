"""DynamoDB/S3 persistence used when WATCHDOG_STORAGE_PROVIDER=dynamodb."""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

SUCCESS_STATUSES = {"complete", "partial", "stale"}

logger = logging.getLogger(__name__)

# GSI required on the evidence table (partition key item_type, sort key
# detected_at, ALL projection) so `read_all_changes` can Query the much
# smaller "change" partition directly instead of Scanning every item in the
# table (which also holds one "snapshot" item per source per refresh cycle).
# See deploy/AWS_PERSISTENCE_SETUP.md for the index-creation command.
#
# item_type only has two values ("change"/"snapshot"), so every change event
# for every source shares one logical partition in this index -- a known
# DynamoDB hot-partition shape. That's an accepted tradeoff for now (it still
# turns an O(table) Scan into an O(change-partition) Query, which is the win
# this index exists for); if evidence volume grows enough to make this
# partition itself a bottleneck, the next step is a higher-cardinality key
# (e.g. bucketed by source_id or a coarse time window), not reverting to Scan.
CHANGES_BY_DETECTED_AT_INDEX = "item_type-detected_at-index"

# Errors DynamoDB returns for a Query against an index that doesn't exist yet
# or is still backfilling (see deploy/AWS_PERSISTENCE_SETUP.md) -- treated as
# "index not ready" rather than a hard failure so a deploy that lands this
# code before the index-creation step (or during its backfill) degrades to
# the old, slower Scan instead of taking the endpoint down entirely.
_INDEX_NOT_READY_ERROR_CODES = {"ValidationException", "ResourceNotFoundException"}


class StorageConfigurationError(RuntimeError):
    pass


def aws_storage_enabled() -> bool:
    return os.getenv("WATCHDOG_STORAGE_PROVIDER", "sqlite").strip().lower() == "dynamodb"


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise StorageConfigurationError(
            f"{name} is required when WATCHDOG_STORAGE_PROVIDER=dynamodb"
        )
    return value


def _decimalize(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _decimalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decimalize(item) for item in value]
    return value


def _native(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {key: _native(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_native(item) for item in value]
    return value


def _scan_all(table, **kwargs) -> list[dict[str, Any]]:
    items = []
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        key = response.get("LastEvaluatedKey")
        if not key:
            return items
        kwargs["ExclusiveStartKey"] = key


def _query_all(table, **kwargs) -> list[dict[str, Any]]:
    items = []
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        key = response.get("LastEvaluatedKey")
        if not key:
            return items
        kwargs["ExclusiveStartKey"] = key


class AwsEvidenceStore:
    def __init__(self, *, table=None, s3_client=None, table_name=None, bucket=None):
        region = os.getenv("AWS_REGION", "us-east-1")
        self.table_name = table_name or _required("WATCHDOG_EVIDENCE_TABLE")
        self.bucket = bucket or _required("EVIDENCE_BUCKET")
        self.table = table or boto3.resource("dynamodb", region_name=region).Table(self.table_name)
        self.s3 = s3_client or boto3.client("s3", region_name=region)

    @staticmethod
    def source_scope(source_id: str, scope: str) -> str:
        return f"{source_id}#{scope}"

    def load_previous_success(self, source_id: str, scope: str) -> dict[str, Any] | None:
        kwargs = {
            "KeyConditionExpression": (
                Key("source_scope").eq(self.source_scope(source_id, scope))
                & Key("record_key").begins_with("SNAPSHOT_SUCCESS#")
            ),
            "ScanIndexForward": False,
        }
        while True:
            response = self.table.query(**kwargs)
            for item in response.get("Items", []):
                if item.get("status") != "complete":
                    continue
                payload = self.s3.get_object(
                    Bucket=self.bucket, Key=item["records_s3_key"]
                )["Body"].read()
                return {"snapshot_id": item["record_key"], "records": json.loads(payload)}
            key = response.get("LastEvaluatedKey")
            if not key:
                return None
            kwargs["ExclusiveStartKey"] = key

    def save_snapshot(self, *, source_id: str, scope: str, captured_at: str, status: str,
                      checksum: str | None, payload: str, error: str | None,
                      previous_snapshot_id: str | None) -> str:
        unique = uuid4().hex
        prefix = "SNAPSHOT_SUCCESS" if status in SUCCESS_STATUSES else "SNAPSHOT_FAILED"
        record_key = f"{prefix}#{captured_at}#{unique}"
        object_key = f"snapshots/{source_id}/{scope}/{captured_at.replace(':', '-')}-{unique}.json"
        self.s3.put_object(Bucket=self.bucket, Key=object_key, Body=payload.encode(),
                           ContentType="application/json", ServerSideEncryption="AES256")
        item = {
            "source_scope": self.source_scope(source_id, scope), "record_key": record_key,
            "item_type": "snapshot", "source_id": source_id, "scope": scope,
            "captured_at": captured_at, "status": status, "records_s3_key": object_key,
            "record_count": len(json.loads(payload)),
        }
        if checksum is not None:
            item["checksum"] = checksum
        if error is not None:
            item["error"] = error
        if previous_snapshot_id is not None:
            item["previous_snapshot_id"] = previous_snapshot_id
        self.table.put_item(Item=item)
        return record_key

    def save_changes(self, *, snapshot_id: str, previous_snapshot_id: str | None,
                     source_id: str, scope: str, detected_at: str,
                     changes: list[dict[str, Any]]) -> None:
        for change in changes:
            unique = uuid4().hex
            item = {
                "source_scope": self.source_scope(source_id, scope),
                "record_key": f"CHANGE#{detected_at}#{unique}", "item_type": "change",
                "snapshot_id": snapshot_id, "source_id": source_id, "scope": scope,
                "detected_at": detected_at, **change,
            }
            if previous_snapshot_id is not None:
                item["previous_snapshot_id"] = previous_snapshot_id
            self.table.put_item(Item=_decimalize(item))

    def read_all_changes(self, *, source_id: str | None = None) -> list[dict[str, Any]]:
        # Query the item_type=change partition of CHANGES_BY_DETECTED_AT_INDEX
        # instead of Scanning the whole table (which also holds one
        # "snapshot" item per source per refresh cycle, typically far more
        # numerous than change events) -- see the index's docstring above.
        expression = Attr("suppressed").not_exists() | Attr("suppressed").eq(False)
        if source_id:
            expression = expression & Attr("source_id").eq(source_id)
        try:
            items = _query_all(
                self.table,
                IndexName=CHANGES_BY_DETECTED_AT_INDEX,
                KeyConditionExpression=Key("item_type").eq("change"),
                FilterExpression=expression,
                ScanIndexForward=False,
            )
            # Query with ScanIndexForward=False already returns items
            # newest-first, and the suppressed check below only excludes
            # items rather than reordering them, so no re-sort is needed.
            need_sort = False
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in _INDEX_NOT_READY_ERROR_CODES:
                raise
            logger.warning(
                "CHANGES_BY_DETECTED_AT_INDEX unavailable (%s) -- falling back to a full "
                "table scan. Create the GSI per deploy/AWS_PERSISTENCE_SETUP.md.",
                exc.response.get("Error", {}).get("Code"),
            )
            items = _scan_all(
                self.table,
                FilterExpression=Attr("item_type").eq("change") & expression,
            )
            need_sort = True  # Scan makes no ordering guarantee.

        ordered = (_native(item) for item in items if not item.get("suppressed", False))
        return sorted(ordered, key=lambda item: item["detected_at"], reverse=True) if need_sort else list(ordered)

    def read_changes(self, *, limit: int, source_id: str | None = None) -> list[dict[str, Any]]:
        return self.read_all_changes(source_id=source_id)[:limit]

    def review_change(self, *, source_scope: str, record_key: str, action: str,
                      reviewed_by: str, note: str = "") -> dict[str, Any]:
        names = {"#review_status": "review_status"}
        values = {
            ":review_status": action,
            ":reviewed_at": datetime.now(timezone.utc).isoformat(),
            ":reviewed_by": reviewed_by,
            ":change_type": "change",
        }
        update = "SET #review_status=:review_status, reviewed_at=:reviewed_at, reviewed_by=:reviewed_by"
        if note:
            values[":review_note"] = note
            update += ", review_note=:review_note"
        try:
            response = self.table.update_item(
                Key={"source_scope": source_scope, "record_key": record_key},
                UpdateExpression=update,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ConditionExpression="item_type=:change_type",
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return {"error": "Evidence finding was not found"}
            raise
        return _native(response["Attributes"])


class AwsFlaggedTractStore:
    def __init__(self, *, table=None, table_name=None):
        region = os.getenv("AWS_REGION", "us-east-1")
        self.table_name = table_name or _required("FLAGGED_TRACTS_TABLE")
        self.table = table or boto3.resource("dynamodb", region_name=region).Table(self.table_name)

    def flag(self, tract_fips: str, recommendation_type: str, source_agent: str,
             population=None, centroid_lat=None, centroid_lon=None, note="") -> dict:
        item = {
            "tract_key": tract_fips, "recommendation_type": recommendation_type,
            "tract_fips": tract_fips, "source_agent": source_agent, "population": population,
            "centroid_lat": centroid_lat, "centroid_lon": centroid_lon,
            "flagged_date": datetime.now(timezone.utc).date().isoformat(), "status": "pending",
            "last_checked_date": None, "note": note,
        }
        item = {key: value for key, value in item.items() if value is not None}
        self.table.put_item(Item=_decimalize(item))
        return _native(item)

    def read(self, status: str) -> list[dict]:
        items = _scan_all(self.table, FilterExpression=Attr("status").eq(status))
        return sorted((_native(item) for item in items), key=lambda item: item["flagged_date"])

    def update(self, tract_fips: str, recommendation_type: str, status: str, note="") -> dict:
        names, values = {"#status": "status"}, {":status": status, ":checked": datetime.now(timezone.utc).isoformat()}
        update = "SET #status=:status, last_checked_date=:checked"
        if note:
            values[":note"] = note
            update += ", note=:note"
        try:
            response = self.table.update_item(
                Key={"tract_key": tract_fips, "recommendation_type": recommendation_type},
                UpdateExpression=update, ExpressionAttributeNames=names,
                ExpressionAttributeValues=values, ConditionExpression="attribute_exists(tract_key)",
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return {"error": f"No flagged row for tract_fips={tract_fips!r}, recommendation_type={recommendation_type!r}"}
            raise
        return _native(response["Attributes"])
