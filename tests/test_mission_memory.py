from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import require_staff_user
from api.operations_read import router
from storage.mission_memory import (
    DynamoDBMissionMemory,
    MissionMemoryConflict,
    get_mission_memory,
)


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression):
        key = (Item["entity_type"], Item["entity_id"])
        if key in self.items:
            error = RuntimeError("duplicate")
            error.response = {
                "Error": {"Code": "ConditionalCheckFailedException"}
            }
            raise error
        self.items[key] = Item

    def query(self, **kwargs):
        return {
            "Items": [
                item
                for (entity_type, _), item in self.items.items()
                if entity_type == "mission_review"
            ]
        }


class FakeDynamoDB:
    def __init__(self, table):
        self.table = table

    def Table(self, name):
        return self.table


def _memory():
    table = FakeTable()
    return DynamoDBMissionMemory(
        table_name="operations", dynamodb_resource=FakeDynamoDB(table)
    ), table


def _mission(status="Ready"):
    return {
        "mission_id": "mission-1",
        "status": status,
        "route": {
            "selected_stops": [
                {"tract_fips": "17031010100"},
                {"stop_id": "17031010200"},
            ]
        },
        "suggested_load": [
            {"item_id": "PRD-001", "quantity": 2, "weight_lbs": 24.0}
        ],
        "human_review_required": True,
        "dispatch_enabled": False,
        "not_for_real_dispatch": True,
    }


def test_only_approved_reviews_are_returned_as_memory():
    memory, _ = _memory()
    memory.record_review(
        mission_id="mission-1",
        action="approve",
        mission=_mission(),
        reviewed_by="staff@example.com",
        study_area="chicago_neighborhoods",
        request_intent={"categories": ["produce"], "load_lbs": 24.0},
    )
    rejected = _mission()
    rejected["mission_id"] = "mission-2"
    memory.record_review(
        mission_id="mission-2",
        action="reject",
        mission=rejected,
        reviewed_by="staff@example.com",
        note="Use a different stop",
        study_area="chicago_neighborhoods",
        request_intent={"categories": ["produce"]},
    )

    results = memory.list_approved(
        study_area="chicago_neighborhoods", categories=["produce"]
    )

    assert [item["mission_id"] for item in results] == ["mission-1"]
    assert results[0]["selected_stop_ids"] == ["17031010100", "17031010200"]
    assert results[0]["suggested_item_ids"] == ["PRD-001"]


def test_memory_converts_floats_before_dynamodb_write():
    memory, table = _memory()
    memory.record_review(
        mission_id="mission-1",
        action="approve",
        mission=_mission(),
        reviewed_by="staff@example.com",
        request_intent={"load_lbs": 24.5},
    )

    item = table.items[("mission_review", "mission-1")]
    assert item["request_intent"]["load_lbs"] == Decimal("24.5")
    assert item["suggested_load"][0]["weight_lbs"] == Decimal("24.0")
    assert "mission_snapshot" not in item


def test_memory_rejects_blocked_approval_and_unguarded_draft():
    memory, _ = _memory()
    with pytest.raises(ValueError, match="blocked"):
        memory.record_review(
            mission_id="mission-1",
            action="approve",
            mission=_mission("Blocked"),
            reviewed_by="staff@example.com",
        )

    unguarded = _mission()
    unguarded["not_for_real_dispatch"] = False
    with pytest.raises(ValueError, match="synthetic-demo guard"):
        memory.record_review(
            mission_id="mission-1",
            action="reject",
            mission=unguarded,
            reviewed_by="staff@example.com",
        )


def test_mission_review_is_immutable():
    memory, _ = _memory()
    memory.record_review(
        mission_id="mission-1",
        action="reject",
        mission=_mission(),
        reviewed_by="staff@example.com",
    )

    with pytest.raises(MissionMemoryConflict):
        memory.record_review(
            mission_id="mission-1",
            action="approve",
            mission=_mission(),
            reviewed_by="staff@example.com",
        )


def test_review_endpoint_uses_verified_staff_identity():
    memory, table = _memory()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_staff_user] = lambda: {
        "email": "reviewer@example.com"
    }
    app.dependency_overrides[get_mission_memory] = lambda: memory
    client = TestClient(app)

    response = client.post(
        "/api/operations/missions/mission-1/review",
        json={
            "action": "approve",
            "mission": _mission(),
            "study_area": "chicago_neighborhoods",
            "request_intent": {"categories": ["produce"]},
        },
    )

    assert response.status_code == 200
    assert response.json()["memory_eligible"] is True
    assert table.items[("mission_review", "mission-1")]["reviewed_by"] == "reviewer@example.com"


def test_memory_endpoint_never_returns_rejected_draft():
    memory, _ = _memory()
    memory.record_review(
        mission_id="mission-1",
        action="reject",
        mission=_mission(),
        reviewed_by="staff@example.com",
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_staff_user] = lambda: {
        "email": "reviewer@example.com"
    }
    app.dependency_overrides[get_mission_memory] = lambda: memory

    response = TestClient(app).get("/api/operations/mission-memory")

    assert response.status_code == 200
    assert response.json() == []
