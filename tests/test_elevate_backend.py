import io
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import api.main as api_main
import services.crew_lead as crew_lead
from services.demo_feedback import compute_demo_feedback
from services.load_recommendation import recommend_load
from storage.s3_inventory import InventoryStoreError, S3InventoryStore


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = kwargs["Body"]
        self.puts.append(kwargs)


def test_s3_inventory_upsert_updates_current_and_writes_a_version():
    s3 = FakeS3()
    store = S3InventoryStore(bucket="test-bucket", client=s3)
    item = store.upsert_on_hand({
        "item_id": "produce", "item_name": "Fresh produce — mixed",
        "category": "produce", "quantity": 120, "unit": "lbs",
    })
    assert item["last_updated"].endswith("+00:00")
    assert store.on_hand()[0]["item_name"] == "Fresh produce — mixed"
    assert len(s3.puts) == 2
    assert s3.puts[0]["Key"].startswith("inventory/versions/")
    assert s3.puts[1]["Key"] == "inventory/on-hand.json"


def test_s3_inventory_permission_failure_is_not_treated_as_empty():
    class Denied:
        def get_object(self, **_kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject")

    with pytest.raises(InventoryStoreError, match="Unable to read"):
        S3InventoryStore(bucket="test-bucket", client=Denied()).on_hand()


def test_load_recommendation_never_invents_case_weight_and_blocks_risky_lots():
    result = recommend_load(
        route={"selected_stops": [{"stop_id": "a", "tract_fips": "1", "demand": 60},
                                  {"stop_id": "b", "tract_fips": "2", "demand": 40}]},
        inventory=[
            {"item_id": "dry", "item_name": "Rice", "category": "dry_goods", "quantity": 100, "unit": "lbs"},
            {"item_id": "cases", "item_name": "Cans", "category": "dry_goods", "quantity": 10, "unit": "cases"},
            {"item_id": "dairy", "item_name": "Dairy", "category": "cold-chain", "quantity": 50, "unit": "lbs"},
        ],
        cold_chain=[{"item_id": "dairy", "status": "At risk"}],
        vehicle_capacity_lbs=150,
        requested_load_lbs=120,
    )
    assert result["assigned_load_lbs"] == 100
    assert result["reserve_lbs"] == 50
    assert {row["item_id"] for row in result["excluded_items"]} == {"cases", "dairy"}
    assert result["stop_allocations"][0]["allocated_lbs"] == 60


def test_study_area_and_constraints_are_explicit():
    assert crew_lead.resolve_study_area("Serve rural Kane County", None) == ("rural_fringe", True)
    assert crew_lead.resolve_study_area("Serve Chicago", "chicago_neighborhoods") == (
        "chicago_neighborhoods", False
    )
    assert crew_lead.parse_constraints("Plan 3.5 hours with 1,200 lbs") == (1200.0, 3.5)
    with pytest.raises(ValueError, match="load in pounds"):
        crew_lead.parse_constraints("Plan a four hour route", time_window_hours=4)


def test_crew_lead_runs_in_fixed_order_and_returns_structured_steps(monkeypatch):
    calls = []
    monkeypatch.setattr(crew_lead, "_run_sentry", lambda area: calls.append("sentry") or {
        "operational_finding_count": 0, "source_health_alert_count": 1
    })
    monkeypatch.setattr(crew_lead, "_run_scout", lambda area, scenario, inferred, top_n, sentry: calls.append("scout") or {
        "summary": "Assumed study area: chicago_neighborhoods (not specified in request). Ranked 2 tracts; top 2 selected.",
        "ranked_tracts": [{"tract_fips": "1"}, {"tract_fips": "2"}],
    })
    monkeypatch.setattr(crew_lead, "_run_router", lambda *args: calls.append("router") or {
        "route": {"status": "optimal", "selected_stops": [{"stop_id": "1"}], "route_minutes": 90}
    })
    monkeypatch.setattr(crew_lead, "_run_dispatch", lambda *args: calls.append("dispatch") or {
        "suggested_load": {"assigned_load_lbs": 500}, "readiness_checks": []
    })
    result = crew_lead.run_crew_brief("Plan a 3 hour Chicago route with 500 lbs", inventory_store=object())
    assert calls == ["sentry", "scout", "router", "dispatch"]
    assert [step["agent"] for step in result["steps"]] == ["sentry", "scout", "router", "dispatch"]
    assert result["steps"][1]["summary"].startswith("Assumed study area:")
    assert result["mission_id"].startswith("LM-")


def test_crew_lead_stops_after_failed_scout(monkeypatch):
    monkeypatch.setattr(crew_lead, "_run_sentry", lambda area: {
        "operational_finding_count": 0, "source_health_alert_count": 0
    })
    monkeypatch.setattr(crew_lead, "_run_scout", lambda *args: (_ for _ in ()).throw(RuntimeError("no snapshot")))
    monkeypatch.setattr(crew_lead, "_run_router", lambda *args: pytest.fail("Router must not run"))
    result = crew_lead.run_crew_brief("Plan a 3 hour Chicago route with 500 lbs")
    assert [step["agent"] for step in result["steps"]] == ["sentry", "scout"]
    assert result["steps"][-1]["status"] == "failed"
    assert result["mission_id"] is None


def test_demo_feedback_uses_real_weighted_formula_without_persisting():
    tract = {
        "tract_fips": "17031840000", "need_score": 50.0, "households_total": 1000,
        "scoring_context_version": "v1",
        "score_components": {
            "food_access_gap": 80, "poverty": 60, "no_vehicle": 40,
            "population_served": 50, "transit_burden": 30, "existing_coverage": 10,
        },
        "weights_used": {
            "food_access_gap": .25, "poverty": .2, "no_vehicle": .15,
            "population_served": .2, "transit_burden": .1, "existing_coverage": .1,
        },
    }
    result = compute_demo_feedback("17031840000", 100, scored_tract=tract)
    assert result["existing_coverage_after"] == 20
    assert result["after"] < result["before"]
    assert result["illustrative_only"] is True
    assert result["persisted"] is False


def test_new_fastapi_contracts(monkeypatch):
    class FakeStore:
        def on_hand(self): return [{"item_id": "rice"}]
        def cold_chain(self): return [{"lot_id": "milk", "status": "OK"}]
        def upsert_on_hand(self, row): return {**row, "last_updated": "now"}
        def upsert_cold_chain(self, row): return {**row, "last_updated": "now"}

    api_main.app.dependency_overrides[api_main.get_inventory_store] = FakeStore
    monkeypatch.setattr(api_main, "run_crew_brief", lambda request, **kwargs: {
        "steps": [], "mission_id": "LM-1", "final_summary": request
    })
    monkeypatch.setattr(api_main, "compute_demo_feedback", lambda tract_id, households: {
        "tract_id": tract_id, "households_served": households, "persisted": False
    })
    client = TestClient(api_main.app)
    try:
        assert client.get("/ping").json() == {"status": "Healthy"}
        assert client.get("/inventory").json() == [{"item_id": "rice"}]
        brief = client.post("/invocations", json={
            "request": "Plan 3 hours and 500 lbs", "study_area": "chicago_neighborhoods"
        })
        assert brief.status_code == 200 and brief.json()["mission_id"] == "LM-1"
        feedback = client.post("/demo/feedback", json={
            "tract_id": "17031840000", "households_served": 25
        })
        assert feedback.status_code == 200 and feedback.json()["persisted"] is False
    finally:
        api_main.app.dependency_overrides.clear()
