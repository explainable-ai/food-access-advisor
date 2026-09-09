"""Human-approved mission memory backed by the operations DynamoDB table."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Any, Literal

import boto3
from boto3.dynamodb.conditions import Key

from storage.operations_repository import DEFAULT_TABLE_NAME


ReviewAction = Literal["approve", "reject"]
MISSION_REVIEW_TYPE = "mission_review"
_BLOCKED_STATUSES = {"blocked"}
_PARTIAL_STATUSES = {"partial", "unknown"}


class MissionMemoryError(RuntimeError):
    """Raised when reviewed mission memory is invalid or unavailable."""


class MissionMemoryConflict(MissionMemoryError):
    """Raised when a mission already has an immutable human review."""


def _dynamodb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _dynamodb_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dynamodb_safe(item) for item in value]
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _identity(item: dict[str, Any]) -> str:
    return str(item.get("item_id") or item.get("sku") or item.get("item") or "").strip()


def _stop_identity(stop: dict[str, Any]) -> str:
    return str(stop.get("tract_fips") or stop.get("stop_id") or "").strip()


def _derived_status(readiness_checks: Any) -> str | None:
    if not isinstance(readiness_checks, list) or not readiness_checks:
        return None
    statuses = [
        str(check.get("status", "")).strip().lower()
        for check in readiness_checks
        if isinstance(check, dict)
    ]
    if not statuses:
        return None
    if any(status in _BLOCKED_STATUSES for status in statuses):
        return "Blocked"
    if any(status in _PARTIAL_STATUSES for status in statuses):
        return "Partial"
    return "Ready"


class DynamoDBMissionMemory:
    """Persist immutable staff reviews and retrieve approved summaries only."""

    def __init__(
        self,
        table_name: str | None = None,
        dynamodb_resource: Any | None = None,
    ):
        self.table_name = table_name or os.getenv(
            "FOOD_ACCESS_OPERATIONS_TABLE", DEFAULT_TABLE_NAME
        )
        if dynamodb_resource is None:
            region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
            dynamodb_resource = boto3.resource("dynamodb", region_name=region)
        self._table = dynamodb_resource.Table(self.table_name)

    @staticmethod
    def _validate_review(
        mission_id: str,
        action: ReviewAction,
        mission: dict[str, Any],
    ) -> None:
        if str(mission.get("mission_id") or "") != mission_id:
            raise ValueError("Mission ID in the review must match the request path")
        if mission.get("human_review_required") is not True:
            raise ValueError("Only human-reviewable mission drafts can be reviewed")
        if mission.get("dispatch_enabled") is not False:
            raise ValueError("Reviewed demo missions must keep dispatch disabled")
        if mission.get("not_for_real_dispatch") is not True:
            raise ValueError("Reviewed missions must retain the synthetic-demo guard")
        status = str(mission.get("status") or "").strip()
        derived_status = _derived_status(mission.get("readiness_checks"))
        if action == "approve":
            if derived_status is None:
                raise ValueError(
                    "Approved mission reviews require readiness checks from the issued draft"
                )
            if status and derived_status != status:
                raise ValueError(
                    "Mission status must match readiness checks from the issued draft"
                )
            if derived_status == "Blocked":
                raise ValueError("A blocked mission cannot become approved memory")

    def record_review(
        self,
        *,
        mission_id: str,
        action: ReviewAction,
        mission: dict[str, Any],
        reviewed_by: str,
        note: str = "",
        study_area: str | None = None,
        request_intent: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store one immutable review; only approvals use the memory partition."""
        self._validate_review(mission_id, action, mission)
        reviewed_at = datetime.now(timezone.utc).isoformat()
        route = mission.get("route") or {}
        compact_load = [
            {
                key: product.get(key)
                for key in (
                    "item_id",
                    "sku",
                    "item",
                    "quantity",
                    "unit_weight_lbs",
                    "weight_lbs",
                )
                if product.get(key) is not None
            }
            for product in mission.get("suggested_load") or []
            if isinstance(product, dict)
        ]
        item = {
            "entity_type": MISSION_REVIEW_TYPE,
            "entity_id": mission_id,
            "mission_id": mission_id,
            "review_action": action,
            "reviewed_at": reviewed_at,
            "reviewed_by": reviewed_by,
            "review_note": note.strip(),
            "study_area": study_area,
            "request_intent": request_intent or {},
            "selected_stop_ids": [
                value
                for stop in route.get("selected_stops") or []
                if (value := _stop_identity(stop))
            ],
            "suggested_item_ids": [
                value
                for product in mission.get("suggested_load") or []
                if (value := _identity(product))
            ],
            "suggested_load": compact_load,
            "route_metrics": {
                key: route.get(key)
                for key in ("route_minutes", "capacity_used", "travel_time_source")
                if route.get(key) is not None
            },
            "readiness_statuses": [
                {
                    "check": check.get("check"),
                    "status": check.get("status"),
                }
                for check in mission.get("readiness_checks") or []
                if isinstance(check, dict)
            ],
            "mission_status": mission.get("status"),
            "data_classification": "synthetic_demo",
            "not_for_real_dispatch": True,
        }
        try:
            self._table.put_item(
                Item=_dynamodb_safe(item),
                ConditionExpression="attribute_not_exists(entity_id)",
            )
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code == "ConditionalCheckFailedException":
                raise MissionMemoryConflict(
                    f"Mission {mission_id} already has a recorded review"
                ) from exc
            raise MissionMemoryError(
                f"Could not record review for mission {mission_id}"
            ) from exc
        return _json_safe(item)

    def list_approved(
        self,
        *,
        study_area: str | None = None,
        categories: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return compact approved-memory summaries, never rejected drafts."""
        items: list[dict[str, Any]] = []
        request: dict[str, Any] = {
            "KeyConditionExpression": Key("entity_type").eq(MISSION_REVIEW_TYPE),
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
            raise MissionMemoryError("Could not read approved mission memory") from exc

        requested_categories = set(categories or [])
        summaries = []
        for raw in items:
            item = _json_safe(raw)
            if item.get("review_action") != "approve":
                continue
            if item.get("data_classification") != "synthetic_demo":
                continue
            if item.get("not_for_real_dispatch") is not True:
                continue
            if study_area and item.get("study_area") != study_area:
                continue
            memory_categories = set(
                (item.get("request_intent") or {}).get("categories") or []
            )
            if requested_categories and not requested_categories.issubset(memory_categories):
                continue
            summaries.append(
                {
                    key: item.get(key)
                    for key in (
                        "mission_id",
                        "reviewed_at",
                        "study_area",
                        "request_intent",
                        "selected_stop_ids",
                        "suggested_item_ids",
                        "suggested_load",
                        "route_metrics",
                        "readiness_statuses",
                        "mission_status",
                        "review_note",
                    )
                }
            )
        summaries.sort(key=lambda item: str(item.get("reviewed_at") or ""), reverse=True)
        return summaries[: max(1, min(limit, 20))]


@lru_cache(maxsize=1)
def get_mission_memory() -> DynamoDBMissionMemory:
    return DynamoDBMissionMemory()
