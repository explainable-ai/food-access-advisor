from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.operations import router


def client(monkeypatch):
    monkeypatch.setenv("FOOD_ACCESS_AUTH_MODE", "disabled")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def payload(investigation_id: str):
    return {
        "investigation_id": investigation_id,
        "investigation_status": "approved",
        "tract_fips": "17031838100",
        "community": "West Englewood",
        "service_date": "2026-09-15",
        "expected_households": 120,
    }


def test_inventory_endpoint_uses_four_state_contract(monkeypatch):
    response = client(monkeypatch).get(
        "/api/inventory/availability",
        params={"expected_households": 120, "service_date": "2026-09-15"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "partial"


def test_complete_api_path_requires_explicit_approval(monkeypatch):
    http = client(monkeypatch)
    created = http.post("/api/missions", json=payload("INV-API-001"))
    assert created.status_code == 201
    mission = created.json()

    blocked = http.post(f"/api/missions/{mission['mission_id']}/plan", json={}).json()
    assert blocked["status"] == "blocked"

    ready = http.post(
        f"/api/missions/{mission['mission_id']}/plan",
        json={"approved_substitutions": {"MILK-COLD": "MILK-UHT"}},
    ).json()
    assert ready["status"] == "ready_for_approval"

    premature = http.post(
        f"/api/missions/{mission['mission_id']}/dispatch",
        json={"expected_version": ready["version"]},
    )
    assert premature.status_code == 422

    approved = http.post(
        f"/api/missions/{mission['mission_id']}/approve",
        json={"expected_version": ready["version"], "note": "Approved demo mission"},
    )
    assert approved.status_code == 200
    approved_mission = approved.json()

    dispatched = http.post(
        f"/api/missions/{mission['mission_id']}/dispatch",
        json={"expected_version": approved_mission["version"]},
    )
    assert dispatched.status_code == 200
    assert dispatched.json()["status"] == "dispatched"


def test_rejects_unapproved_investigation(monkeypatch):
    request = payload("INV-API-002")
    request["investigation_status"] = "monitoring"
    response = client(monkeypatch).post("/api/missions", json=request)
    assert response.status_code == 422
    assert "approved investigation" in response.json()["detail"]
