"""Read-only access to the verified synthetic Mission Operations dataset."""

from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


DEFAULT_TABLE_NAME = "food-access-demo-operations"
EXPECTED_DATASET_VERSION = "demo-v1"
EXPECTED_COUNTS = {
    "product": 12,
    "inventory_lot": 30,
    "vehicle": 4,
    "driver": 5,
    "site_partner": 6,
    "permit_rule": 6,
    "permit": 6,
    "manifest_policy": 8,
    "demo_scenario": 4,
}


class OperationsDataError(RuntimeError):
    """Raised when Mission Operations data is unavailable or unsafe."""


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


class OperationsRepository:
    """Query the demo table without exposing any write capability."""

    def __init__(self, table_name: str | None = None, dynamodb_resource: Any | None = None):
        self.table_name = table_name or os.getenv(
            "FOOD_ACCESS_OPERATIONS_TABLE", DEFAULT_TABLE_NAME
        )
        if dynamodb_resource is None:
            region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
            dynamodb_resource = boto3.resource("dynamodb", region_name=region)
        self._table = dynamodb_resource.Table(self.table_name)

    @staticmethod
    def _validate(item: dict[str, Any], expected_type: str) -> dict[str, Any]:
        if item.get("entity_type") != expected_type:
            raise OperationsDataError(f"Unexpected entity type for {item.get('entity_id', '<unknown>')}")
        if item.get("dataset_version") != EXPECTED_DATASET_VERSION:
            raise OperationsDataError(f"Unsupported dataset version for {item.get('entity_id', '<unknown>')}")
        if item.get("data_classification") != "synthetic_demo":
            raise OperationsDataError(f"Unsafe data classification for {item.get('entity_id', '<unknown>')}")
        if item.get("not_for_real_dispatch") is not True:
            raise OperationsDataError(f"Real-dispatch guard is missing for {item.get('entity_id', '<unknown>')}")
        return _json_safe(item)

    def list_entities(self, entity_type: str) -> list[dict[str, Any]]:
        if entity_type not in EXPECTED_COUNTS:
            raise OperationsDataError(f"Unsupported entity type: {entity_type}")
        items: list[dict[str, Any]] = []
        request: dict[str, Any] = {
            "KeyConditionExpression": Key("entity_type").eq(entity_type),
            "ConsistentRead": True,
        }
        try:
            while True:
                response = self._table.query(**request)
                items.extend(response.get("Items", []))
                cursor = response.get("LastEvaluatedKey")
                if not cursor:
                    break
                request["ExclusiveStartKey"] = cursor
        except Exception as exc:
            raise OperationsDataError(f"Could not read {entity_type} records") from exc
        validated = [self._validate(item, entity_type) for item in items]
        return sorted(validated, key=lambda item: item["entity_id"])

    def get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        if entity_type not in EXPECTED_COUNTS:
            raise OperationsDataError(f"Unsupported entity type: {entity_type}")
        try:
            response = self._table.get_item(
                Key={"entity_type": entity_type, "entity_id": entity_id},
                ConsistentRead=True,
            )
        except Exception as exc:
            raise OperationsDataError(f"Could not read {entity_type} {entity_id}") from exc
        item = response.get("Item")
        return self._validate(item, entity_type) if item else None

    def summary(self) -> dict[str, Any]:
        counts = {entity_type: len(self.list_entities(entity_type)) for entity_type in EXPECTED_COUNTS}
        return {
            "table": self.table_name,
            "dataset_version": EXPECTED_DATASET_VERSION,
            "data_classification": "synthetic_demo",
            "not_for_real_dispatch": True,
            "counts": counts,
            "total_records": sum(counts.values()),
            "expected_counts_match": counts == EXPECTED_COUNTS,
        }


@lru_cache(maxsize=1)
def get_operations_repository() -> OperationsRepository:
    return OperationsRepository()
