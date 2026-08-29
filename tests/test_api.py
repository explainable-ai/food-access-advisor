"""Tests for the FastAPI backend -- no real Bedrock/Overpass calls.
route_request is mocked for the two Advisor endpoints (same
no-network-in-tests philosophy as the rest of this repo); the
flagged-tracts/impact-metrics endpoints use a temp-file DB, same pattern
as test_flagged_tracts.py / test_impact_metrics.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import api.main as api_main  # noqa: E402
import tools.flagged_tracts as flagged_tracts  # noqa: E402
from tools.existing_resources import OverpassQueryError  # noqa: E402

client = TestClient(api_main.app)


def test_health_endpoint_has_no_dependencies():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def use_temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "flagged_tracts_test.db"
    monkeypatch.setattr(flagged_tracts, "DB_PATH", db_path)
    return db_path


class FakeNodeResult:
    def __init__(self, text):
        self.result = text  # str(self.result) below just needs str() to work


class FakeGraphResult:
    def __init__(self, node_id, text):
        self.results = {node_id: FakeNodeResult(text)}


def test_site_advisor_returns_answer_text(monkeypatch):
    monkeypatch.setattr(
        api_main,
        "route_request",
        lambda mode, question: FakeGraphResult("site_advisor", "Tract 17031840000 ranks highest."),
    )

    response = client.post("/api/site-advisor", json={"question": "where's the highest-need spot?"})

    assert response.status_code == 200
    assert response.json() == {"answer": "Tract 17031840000 ranks highest."}


def test_route_advisor_returns_answer_text(monkeypatch):
    monkeypatch.setattr(
        api_main,
        "route_request",
        lambda mode, question: FakeGraphResult("route_advisor", "A stop near tract 17003960100 would help most."),
    )

    response = client.post("/api/route-advisor", json={"question": "where would a route change help?"})

    assert response.status_code == 200
    assert response.json() == {"answer": "A stop near tract 17003960100 would help most."}


def test_site_advisor_surfaces_overpass_failure_as_502(monkeypatch):
    def _raise(mode, question):
        raise OverpassQueryError("Overpass API unavailable after 2 attempts")

    monkeypatch.setattr(api_main, "route_request", _raise)

    response = client.post("/api/site-advisor", json={"question": "where's the highest-need spot?"})

    assert response.status_code == 502
    assert "Overpass" in response.json()["detail"]


def test_flagged_tracts_rejects_unknown_status():
    response = client.get("/api/flagged-tracts", params={"status": "made_up_status"})

    assert response.status_code == 400


def test_flagged_tracts_returns_rows_for_valid_status(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )

    response = client.get("/api/flagged-tracts", params={"status": "pending"})

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["tract_fips"] == "17031840000"


def test_site_ranked_tracts_returns_scored_list(monkeypatch):
    monkeypatch.setattr(api_main, "get_existing_resources", lambda: [])

    response = client.get("/api/site-advisor/ranked-tracts")

    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    assert "need_score" in body[0]


def test_site_ranked_tracts_accepts_adjustable_weights(monkeypatch):
    monkeypatch.setattr(api_main, "get_existing_resources", lambda: [])
    response = client.get("/api/site-advisor/ranked-tracts", params={"poverty": 1, "food_access_gap": 0})
    assert response.status_code == 200
    assert response.json()[0]["weights_used"]["poverty"] > 0


def test_site_ranked_tracts_rejects_all_zero_weights(monkeypatch):
    response = client.get("/api/site-advisor/ranked-tracts", params={
        "food_access_gap": 0, "poverty": 0, "no_vehicle": 0,
        "population_served": 0, "transit_burden": 0, "existing_coverage": 0})
    assert response.status_code == 422
    assert "greater than zero" in response.json()["detail"]


def test_route_ranked_tracts_returns_scored_list(monkeypatch):
    monkeypatch.setattr(api_main, "get_rural_existing_resources", lambda: [])

    response = client.get("/api/route-advisor/ranked-tracts")

    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    assert "need_score" in body[0]


def test_site_ranked_tracts_surfaces_overpass_failure_as_502(monkeypatch):
    def _raise():
        raise OverpassQueryError("Overpass API unavailable after 2 attempts")

    monkeypatch.setattr(api_main, "get_existing_resources", _raise)

    response = client.get("/api/site-advisor/ranked-tracts")

    assert response.status_code == 502


def test_site_resources_returns_list(monkeypatch):
    monkeypatch.setattr(
        api_main, "get_existing_resources", lambda: [{"kind": "grocery", "name": "Test", "lat": 41.8, "lon": -87.6}]
    )

    response = client.get("/api/site-advisor/resources")

    assert response.status_code == 200
    assert response.json() == [{"kind": "grocery", "name": "Test", "lat": 41.8, "lon": -87.6}]


def test_route_resources_surfaces_overpass_failure_as_502(monkeypatch):
    def _raise():
        raise OverpassQueryError("boom")

    monkeypatch.setattr(api_main, "get_rural_existing_resources", _raise)

    response = client.get("/api/route-advisor/resources")

    assert response.status_code == 502


def test_route_optimization_endpoint_returns_constrained_plan():
    response = client.post("/api/route-advisor/optimize", json={
        "depot": {"lat": 37.0, "lon": -89.2}, "max_route_minutes": 120,
        "vehicle_capacity": 10, "max_stops": 1, "service_minutes": 10,
        "candidates": [
            {"stop_id": "A", "lat": 37.01, "lon": -89.2, "demand": 5, "need_score": 90},
            {"stop_id": "B", "lat": 37.02, "lon": -89.2, "demand": 5, "need_score": 40}],
        "travel_time_matrix": [[0, 5, 6], [5, 0, 2], [6, 2, 0]],
    })
    assert response.status_code == 200
    assert response.json()["selected_stops"][0]["stop_id"] == "A"


def test_route_optimization_endpoint_rejects_bad_matrix():
    response = client.post("/api/route-advisor/optimize", json={
        "depot": {"lat": 37.0, "lon": -89.2}, "max_route_minutes": 120,
        "vehicle_capacity": 10, "max_stops": 1,
        "candidates": [{"stop_id": "A", "lat": 37.01, "lon": -89.2, "demand": 5, "need_score": 90}],
        "travel_time_matrix": [[0]],
    })
    assert response.status_code == 422


def test_route_optimization_endpoint_uses_amazon_location(monkeypatch):
    monkeypatch.setattr(api_main, "get_amazon_location_matrix", lambda points: [[0, 5], [5, 0]])
    response = client.post("/api/route-advisor/optimize", json={
        "depot": {"lat": 37.0, "lon": -89.2}, "max_route_minutes": 120,
        "vehicle_capacity": 10, "max_stops": 1,
        "candidates": [{"stop_id": "A", "lat": 37.01, "lon": -89.2, "demand": 5, "need_score": 90}],
    })
    assert response.status_code == 200
    assert response.json()["travel_time_source"] == "amazon_location_routes_v2"


def test_route_optimization_endpoint_surfaces_amazon_location_failure(monkeypatch):
    def fail(_points):
        raise api_main.TravelTimeProviderError("routing unavailable")
    monkeypatch.setattr(api_main, "get_amazon_location_matrix", fail)
    response = client.post("/api/route-advisor/optimize", json={
        "depot": {"lat": 37.0, "lon": -89.2}, "max_route_minutes": 120,
        "vehicle_capacity": 10, "max_stops": 1,
        "candidates": [{"stop_id": "A", "lat": 37.01, "lon": -89.2, "demand": 5, "need_score": 90}],
    })
    assert response.status_code == 502


def test_site_evidence_returns_brief_text(monkeypatch):
    monkeypatch.setattr(api_main, "write_evidence_brief", lambda tract: "This tract has high need...")

    response = client.post(
        "/api/site-advisor/evidence",
        json={"tract": {"tract_fips": "17031840000", "need_score": 92.0}},
    )

    assert response.status_code == 200
    assert response.json() == {"brief": "This tract has high need..."}


def test_route_evidence_surfaces_failure_as_502(monkeypatch):
    def _raise(tract):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(api_main, "write_route_brief", _raise)

    response = client.post(
        "/api/route-advisor/evidence",
        json={"tract": {"tract_fips": "17003960100", "need_score": 80.0}},
    )

    assert response.status_code == 502


def test_verify_updates_status(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.update_flagged_tract("17031840000", "site", "possible_change")

    response = client.post(
        "/api/flagged-tracts/verify",
        json={"tract_fips": "17031840000", "recommendation_type": "site", "verification": "verified_open"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "resource_found"


def test_verify_rejects_unknown_verification_at_schema_level(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )

    response = client.post(
        "/api/flagged-tracts/verify",
        json={"tract_fips": "17031840000", "recommendation_type": "site", "verification": "bogus_choice"},
    )

    assert response.status_code == 422  # Literal type rejects it before the handler runs


def test_verify_missing_tract_returns_400(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    response = client.post(
        "/api/flagged-tracts/verify",
        json={"tract_fips": "00000000000", "recommendation_type": "site", "verification": "verified_open"},
    )

    assert response.status_code == 400


def test_impact_metrics_returns_both_regions(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17003960100", recommendation_type="route", source_agent="route_advisor"
    )

    response = client.get("/api/impact-metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["urban"]["total_flagged"] == 1
    assert body["rural"]["total_flagged"] == 1
