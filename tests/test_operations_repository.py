from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.operations_read import router
from storage.operations_repository import (
    EXPECTED_COUNTS,
    OperationsDataError,
    OperationsRepository,
    get_operations_repository,
)


def _item(entity_type, entity_id, **values):
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "dataset_version": "demo-v1",
        "data_classification": "synthetic_demo",
        "not_for_real_dispatch": True,
        **values,
    }


class FakeTable:
    def __init__(self, items):
        self.items = items

    def query(self, **kwargs):
        entity_type = kwargs["KeyConditionExpression"]._values[1]
        return {"Items": [item for item in self.items if item["entity_type"] == entity_type]}

    def get_item(self, Key, **kwargs):
        item = next((item for item in self.items if all(item.get(k) == v for k, v in Key.items())), None)
        return {"Item": item} if item else {}


class FakeDynamoDB:
    def __init__(self, table):
        self.table = table

    def Table(self, name):
        return self.table


def _repository(items):
    return OperationsRepository("test-table", FakeDynamoDB(FakeTable(items)))


def test_lists_entities_stably_and_converts_decimals():
    repository = _repository([
        _item("vehicle", "VEH-002", payload_capacity_lb=Decimal("2200.5")),
        _item("vehicle", "VEH-001", payload_capacity_lb=Decimal("1000")),
    ])
    rows = repository.list_entities("vehicle")
    assert [row["entity_id"] for row in rows] == ["VEH-001", "VEH-002"]
    assert rows[0]["payload_capacity_lb"] == 1000
    assert rows[1]["payload_capacity_lb"] == 2200.5


@pytest.mark.parametrize("field,value", [
    ("dataset_version", "wrong"),
    ("data_classification", "production"),
    ("not_for_real_dispatch", False),
])
def test_fails_closed_for_unsafe_records(field, value):
    item = _item("driver", "DRV-001")
    item[field] = value
    with pytest.raises(OperationsDataError):
        _repository([item]).list_entities("driver")


def test_summary_reports_all_expected_counts():
    items = []
    for entity_type, count in EXPECTED_COUNTS.items():
        items.extend(_item(entity_type, f"{entity_type}-{index:03d}") for index in range(count))
    summary = _repository(items).summary()
    assert summary["counts"] == EXPECTED_COUNTS
    assert summary["total_records"] == 81
    assert summary["expected_counts_match"] is True


def test_read_only_api_lists_and_gets_scenarios():
    repository = _repository([_item("demo_scenario", "DEMO-READY-001", expected_mission_status="Ready for approval")])
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_operations_repository] = lambda: repository
    client = TestClient(app)
    assert client.get("/api/operations/scenarios").status_code == 200
    assert client.get("/api/operations/scenarios/DEMO-READY-001").json()["expected_mission_status"] == "Ready for approval"
    assert client.get("/api/operations/scenarios/missing").status_code == 404



@pytest.mark.parametrize(
    "scenario_id,inventory_answer,expected_status,explanation,canonical_status",
    [
        (
            "DEMO-READY-001",
            "Yes",
            "Ready for approval",
            "All required inventory is current and sufficient; vehicle, driver, site, and demo permit checks pass.",
            "Ready",
        ),
        (
            "DEMO-PARTIAL-002",
            "Partial",
            "Needs substitution approval",
            "Whole milk availability is below the policy requirement; an authorized person must approve a substitution or reduce household count.",
            "Partial",
        ),
        (
            "DEMO-BLOCKED-003",
            "Yes",
            "Blocked",
            "Assigned vehicle is unavailable for overdue maintenance, driver credentials are invalid, and the demo permit is missing.",
            "Blocked",
        ),
        (
            "DEMO-UNKNOWN-004",
            "Unknown",
            "Blocked — refresh required",
            "Rural inventory was last updated more than 24 hours ago; the agent must not treat stale quantities as available.",
            "Unknown",
        ),
    ],
)
def test_mission_preview_normalizes_all_demo_scenarios(
    scenario_id, inventory_answer, expected_status, explanation, canonical_status
):
    references = {
        "vehicle_id": "VEH-001",
        "driver_id": "DRV-001",
        "site_id": "SITE-001",
        "permit_id": "PERMIT-001",
    }
    items = [
        _item(
            "demo_scenario",
            scenario_id,
            scenario_name=scenario_id,
            inventory_answer=inventory_answer,
            expected_mission_status=expected_status,
            expected_agent_explanation=explanation,
            warehouse_id="WH-001",
            expected_households=60,
            **references,
        ),
        _item("vehicle", references["vehicle_id"]),
        _item("driver", references["driver_id"]),
        _item("site_partner", references["site_id"]),
        _item("permit", references["permit_id"]),
    ]
    repository = _repository(items)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_operations_repository] = lambda: repository
    client = TestClient(app)

    response = client.get(f"/api/operations/scenarios/{scenario_id}/preview")

    assert response.status_code == 200
    preview = response.json()
    assert preview["status"] == canonical_status
    assert preview["not_for_real_dispatch"] is True
    assert preview["dispatch_enabled"] is False
    assert preview["explanation"] == explanation
    assert {check["kind"] for check in preview["checks"]} == {
        "inventory",
        "vehicle",
        "driver",
        "site",
        "permit",
        "policy",
    }

    checks = {check["kind"]: check for check in preview["checks"]}
    if canonical_status == "Partial":
        assert checks["inventory"]["status"] == "Partial"
        assert checks["policy"]["status"] == "Partial"
    elif canonical_status == "Blocked":
        assert checks["vehicle"]["status"] == "Blocked"
        assert checks["driver"]["status"] == "Blocked"
        assert checks["permit"]["status"] == "Blocked"
    elif canonical_status == "Unknown":
        assert checks["inventory"]["status"] == "Unknown"
        assert checks["policy"]["status"] == "Unknown"
