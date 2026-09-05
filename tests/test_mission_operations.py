from datetime import UTC, datetime, timedelta

import pytest

from tools.mission_operations import MissionConflictError, MissionOperationsService


def mission_payload(**overrides):
    payload = {
        "investigation_id": "INV-001",
        "investigation_status": "approved",
        "tract_fips": "17031838100",
        "community": "West Englewood",
        "study_area": "chicago",
        "service_date": "2026-09-15",
        "expected_households": 120,
    }
    return {**payload, **overrides}


def test_mission_requires_approved_investigation():
    service = MissionOperationsService(investigation_lookup=lambda _: {"status": "monitoring"})
    with pytest.raises(ValueError, match="approved investigation"):
        service.create_mission(mission_payload())


def test_caller_cannot_forge_investigation_approval():
    service = MissionOperationsService()
    with pytest.raises(ValueError, match="approved investigation"):
        service.create_mission(mission_payload(investigation_id="INV-NOT-FOUND", investigation_status="approved"))


def test_inventory_reports_partial_and_never_hides_shortage():
    service = MissionOperationsService()
    mission = service.create_mission(mission_payload())
    planned = service.plan_mission(mission["mission_id"])
    assert planned["status"] == "blocked"
    assert planned["inventory"]["status"] == "partial"
    milk = next(item for item in planned["inventory"]["items"] if item["sku"] == "MILK-COLD")
    assert milk["required_quantity"] == 30
    assert milk["available_to_promise"] == 18
    assert milk["shortage_quantity"] == 12


def test_approved_substitution_produces_dispatch_ready_plan():
    service = MissionOperationsService()
    mission = service.create_mission(mission_payload())
    planned = service.plan_mission(
        mission["mission_id"], approved_substitutions={"MILK-COLD": "MILK-UHT"}
    )
    assert planned["status"] == "blocked"
    assert planned["inventory"]["status"] == "yes"
    assert planned["vehicle"]["vehicle_id"] == "VEH-REF-01"
    rejected = next(row for row in planned["fleet_evaluation"]["vehicles"] if row["vehicle_id"] == "VEH-CARGO-02")
    assert "temperature zones incompatible" in rejected["reasons"]
    assert planned["site"]["permit"]["status"] == "verified"
    assert "route_not_approved" in planned["blockers"]


def test_dispatch_requires_approval_and_optimistic_version():
    service = MissionOperationsService()
    mission = service.create_mission(mission_payload())
    planned = service.plan_mission(mission["mission_id"], approved_substitutions={"MILK-COLD": "MILK-UHT"})
    with pytest.raises(ValueError, match="approval"):
        service.dispatch_mission(mission["mission_id"], expected_version=planned["version"], actor="ops@example.org")
    routed = service.record_route_approval(
        mission["mission_id"], expected_version=planned["version"], actor="ops@example.org",
        route_id="ROUTE-001", distance_miles=14.2, duration_minutes=38,
    )
    approved = service.approve_mission(
        mission["mission_id"], expected_version=routed["version"], actor="ops@example.org"
    )
    with pytest.raises(MissionConflictError):
        service.dispatch_mission(mission["mission_id"], expected_version=routed["version"], actor="ops@example.org")
    dispatched = service.dispatch_mission(
        mission["mission_id"], expected_version=approved["version"], actor="ops@example.org"
    )
    assert dispatched["status"] == "dispatched"
    assert dispatched["dispatch"]["notification_status"] == "simulated_test_delivery"
    assert dispatched["reservations"]
    assert sum(row["quantity"] for row in dispatched["reservations"] if row["sku"] == "MILK-COLD") == 18
    assert sum(row["quantity"] for row in dispatched["reservations"] if row["sku"] == "MILK-UHT") == 12


def test_substitution_must_stay_in_the_same_product_category():
    service = MissionOperationsService()
    mission = service.create_mission(mission_payload())
    with pytest.raises(ValueError, match="same product category"):
        service.plan_mission(
            mission["mission_id"], approved_substitutions={"MILK-COLD": "PRODUCE-BOX"}
        )


def test_stale_inventory_returns_unknown_not_zero_available():
    anchor = datetime(2026, 9, 4, 12, tzinfo=UTC)
    service = MissionOperationsService(now_fn=lambda: anchor)
    for lot in service._lots:
        lot.updated_at = anchor - timedelta(hours=25)
    mission = service.create_mission(mission_payload())
    planned = service.plan_mission(mission["mission_id"])
    assert planned["inventory"]["status"] == "unknown"
    assert planned["inventory"]["reason"] == "Inventory data is stale"


def test_reconciliation_closes_the_operational_loop():
    service = MissionOperationsService()
    mission = service.create_mission(mission_payload())
    planned = service.plan_mission(mission["mission_id"], approved_substitutions={"MILK-COLD": "MILK-UHT"})
    routed = service.record_route_approval(
        mission["mission_id"], expected_version=planned["version"], actor="ops@example.org",
        route_id="ROUTE-002", distance_miles=14.2, duration_minutes=38,
    )
    approved = service.approve_mission(mission["mission_id"], expected_version=routed["version"], actor="ops@example.org")
    dispatched = service.dispatch_mission(mission["mission_id"], expected_version=approved["version"], actor="ops@example.org")
    reconciled = service.reconcile_mission(
        mission["mission_id"], expected_version=dispatched["version"], actor="ops@example.org",
        outcome={"households_served": 112, "inventory_returned": {"PANTRY-BOX": 8}, "temperature_exception": False},
    )
    assert reconciled["status"] == "reconciled"
    assert reconciled["outcome"]["households_served"] == 112
    pantry_reservations = [row for row in reconciled["reservations"] if row["sku"] == "PANTRY-BOX"]
    assert sum(row["returned_quantity"] for row in pantry_reservations) == 8
    assert all(row["status"] == "reconciled" for row in reconciled["reservations"])
    pantry_lot = next(lot for lot in service._lots if lot.sku == "PANTRY-BOX")
    assert pantry_lot.reserved == 0
    assert pantry_lot.on_hand == 13
    assert reconciled["timeline"][-1]["action"] == "mission_reconciled"


def test_dispatched_or_reconciled_mission_cannot_be_replanned():
    service = MissionOperationsService()
    mission = service.create_mission(mission_payload(expected_households=10))
    planned = service.plan_mission(mission["mission_id"])
    routed = service.record_route_approval(
        mission["mission_id"], expected_version=planned["version"], actor="ops@example.org",
        route_id="ROUTE-003", distance_miles=8, duration_minutes=24,
    )
    approved = service.approve_mission(
        mission["mission_id"], expected_version=routed["version"], actor="ops@example.org"
    )
    dispatched = service.dispatch_mission(
        mission["mission_id"], expected_version=approved["version"], actor="ops@example.org"
    )
    with pytest.raises(ValueError, match="cannot be replanned"):
        service.plan_mission(mission["mission_id"])
    reconciled = service.reconcile_mission(
        mission["mission_id"], expected_version=dispatched["version"], actor="ops@example.org",
        outcome={"households_served": 10, "inventory_returned": {}},
    )
    with pytest.raises(ValueError, match="cannot be replanned"):
        service.plan_mission(reconciled["mission_id"])


def test_dispatch_atomically_rejects_overlapping_vehicle_and_driver_assignment():
    service = MissionOperationsService()
    first = service.create_mission(mission_payload(investigation_id="INV-001", expected_households=10))
    first = service.plan_mission(first["mission_id"])
    first = service.record_route_approval(
        first["mission_id"], expected_version=first["version"], actor="ops@example.org",
        route_id="ROUTE-004", distance_miles=8, duration_minutes=24,
    )
    first = service.approve_mission(first["mission_id"], expected_version=first["version"], actor="ops@example.org")

    service._investigation_lookup = lambda investigation_id: {"investigation_id": investigation_id, "status": "approved"}
    second = service.create_mission(mission_payload(investigation_id="INV-002", expected_households=10))
    second = service.plan_mission(second["mission_id"])
    second = service.record_route_approval(
        second["mission_id"], expected_version=second["version"], actor="ops@example.org",
        route_id="ROUTE-005", distance_miles=9, duration_minutes=26,
    )
    second = service.approve_mission(second["mission_id"], expected_version=second["version"], actor="ops@example.org")
    service.dispatch_mission(first["mission_id"], expected_version=first["version"], actor="ops@example.org")
    with pytest.raises(MissionConflictError, match="already assigned"):
        service.dispatch_mission(second["mission_id"], expected_version=second["version"], actor="ops@example.org")


def test_expired_permit_blocks_service_date():
    service = MissionOperationsService()
    mission = service.create_mission(mission_payload(service_date="2027-01-01", expected_households=10))
    planned = service.plan_mission(mission["mission_id"])
    assert "permit_expired" in planned["blockers"]
