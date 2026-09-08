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
from config import PILOT_RURAL_COUNTY  # noqa: E402
import tools.flagged_tracts as flagged_tracts  # noqa: E402
from tools.existing_resources import OverpassQueryError  # noqa: E402

client = TestClient(api_main.app)


def prepared_urban_tracts():
    return [
        {
            "tract_fips": "17031010100",
            "population": 1000,
            "low_access_half_mile": 1,
            "low_access_one_mile": 1,
            "centroid_lat": 41.88,
            "centroid_lon": -87.63,
            "is_chicago": True,
            "community_area": "North Lawndale",
            "food_insecurity_rate": 0.8,
            "transit_burden": 0.7,
            "scoring_context_version": "food-access-advisor-urban-context-v1",
        },
        {
            "tract_fips": "17031010200",
            "population": 900,
            "low_access_half_mile": 0,
            "low_access_one_mile": 1,
            "centroid_lat": 42.05,
            "centroid_lon": -87.75,
            "is_chicago": False,
            "food_insecurity_rate": 0.2,
            "transit_burden": None,
            "scoring_context_version": "food-access-advisor-urban-context-v1",
        },
    ]


def test_health_endpoint_has_no_dependencies():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert client.get("/ping").json() == {"status": "Healthy"}


def test_crew_brief_returns_structured_live_steps(monkeypatch):
    monkeypatch.setattr(api_main, "run_crew_brief", lambda request, study_area: {
        "steps": [{"agent": "sentry", "status": "failed", "summary": "source unavailable", "data": {}}],
        "mission_id": None,
        "final_summary": "source unavailable",
    })
    response = client.post("/crew/brief", json={"request": "200 lbs in four hours", "study_area": None})
    assert response.status_code == 200
    assert response.json()["steps"][0]["agent"] == "sentry"


def test_agentcore_invoke_uses_same_crew_flow(monkeypatch):
    monkeypatch.setattr(api_main, "run_crew_brief", lambda request, study_area: {"steps": [], "mission_id": None, "final_summary": "done"})
    response = client.post("/invoke", json={"request": "brief the crew"})
    assert response.status_code == 200
    assert response.json()["final_summary"] == "done"
    runtime_response = client.post("/invocations", json={"request": "brief the crew"})
    assert runtime_response.status_code == 200
    assert runtime_response.json()["final_summary"] == "done"


def test_demo_feedback_does_not_claim_persistence(monkeypatch):
    monkeypatch.setattr(api_main, "compute_demo_feedback", lambda tract_id, households: {"tract_id": tract_id, "households_served": households, "persisted": False})
    response = client.post("/demo/feedback", json={"tract_id": "17031010100", "households_served": 20})
    assert response.status_code == 200
    assert response.json()["persisted"] is False


def test_tract_boundaries_returns_filtered_feature_collection_for_county_17063():
    response = client.get("/api/tract-boundaries", params={"county": "17063"})

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) > 0
    assert all(feature["properties"]["tract_fips"].startswith("17063") for feature in body["features"])


def test_tract_boundaries_rejects_unknown_county():
    response = client.get("/api/tract-boundaries", params={"county": "99999"})

    assert response.status_code == 404


def test_rural_scored_tracts_have_boundaries_across_configured_counties(monkeypatch):
    county_boundaries = {}
    merged_boundary_fips = {}
    for county_fips in PILOT_RURAL_COUNTY["county_fips"]:
        response = client.get("/api/tract-boundaries", params={"county": county_fips})
        assert response.status_code == 200
        features = response.json()["features"]
        assert len(features) > 0
        county_boundaries[county_fips] = features
        for feature in features:
            merged_boundary_fips.setdefault(feature["properties"]["tract_fips"], feature)

    sampled_fips = [county_boundaries[county_fips][0]["properties"]["tract_fips"] for county_fips in PILOT_RURAL_COUNTY["county_fips"]]
    for tract_fips in merged_boundary_fips:
        if tract_fips not in sampled_fips:
            sampled_fips.append(tract_fips)
        if len(sampled_fips) == 72:
            break
    assert len(sampled_fips) == 72

    monkeypatch.setattr(api_main, "load_resource_cache", lambda scope, require_complete_coverage=False: [])
    monkeypatch.setattr(
        api_main,
        "get_all_rural_tracts",
        lambda: [
            {
                "tract_fips": tract_fips,
                "population": 1000,
                "low_access_half_mile": 0,
                "low_access_one_mile": 0,
                "centroid_lat": 41.0,
                "centroid_lon": -88.0,
                "low_income_low_access_share": 0.25,
                "data_mode": "real",
            }
            for tract_fips in sampled_fips
        ],
    )

    scored = client.get("/api/route-advisor/tract-scores")
    assert scored.status_code == 200
    scored_fips = {row["tract_fips"] for row in scored.json()}
    assert scored_fips == set(sampled_fips)
    assert not scored_fips.issubset({
        feature["properties"]["tract_fips"]
        for feature in county_boundaries["17089"]
    })
    assert scored_fips.issubset(set(merged_boundary_fips))


def test_cors_allows_local_and_lovable_frontends_but_not_unknown_origin():
    origins = [
        "http://localhost:5173",
        "http://localhost:8080",
        "https://preview--food-equity-navigator.lovable.app",
        "https://food-equity-navigator.lovable.app",
        "https://food-guide-advisor.lovable.app",
        "https://4edb7a89-80c4-4184-bc69-eb628ea0e136.lovableproject.com",
        "https://id-preview--4edb7a89-80c4-4184-bc69-eb628ea0e136.lovable.app",
    ]
    for origin in origins:
        allowed = client.options(
            "/api/watchdog/changes",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        assert allowed.headers["access-control-allow-origin"] == origin

    blocked_origins = [
        "https://untrusted.example",
        "https://attacker-controlled.lovable.app",
        "https://food-equity-navigator.lovableproject.com",
    ]
    for origin in blocked_origins:
        blocked = client.options(
            "/api/watchdog/changes",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        assert "access-control-allow-origin" not in blocked.headers


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
        lambda mode, question: FakeGraphResult("route_advisor", "A stop near tract 17089960100 would help most."),
    )

    response = client.post("/api/route-advisor", json={"question": "where would a route change help?"})

    assert response.status_code == 200
    assert response.json() == {"answer": "A stop near tract 17089960100 would help most."}


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
    monkeypatch.setattr(api_main, "get_all_tracts", prepared_urban_tracts)
    monkeypatch.setattr(
        api_main,
        "load_resource_cache",
        lambda scope, require_complete_coverage=False: [],
    )

    response = client.get("/api/site-advisor/ranked-tracts")

    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    assert "need_score" in body[0]


def test_site_ranked_tracts_accepts_adjustable_weights(monkeypatch):
    monkeypatch.setattr(api_main, "get_all_tracts", prepared_urban_tracts)
    monkeypatch.setattr(
        api_main,
        "load_resource_cache",
        lambda scope, require_complete_coverage=False: [],
    )
    response = client.get("/api/site-advisor/ranked-tracts", params={"poverty": 1, "food_access_gap": 0})
    assert response.status_code == 200
    assert response.json()[0]["weights_used"]["poverty"] > 0


def test_site_scores_default_to_chicago_and_can_include_cook_county(monkeypatch):
    monkeypatch.setattr(api_main, "get_all_tracts", prepared_urban_tracts)
    monkeypatch.setattr(
        api_main,
        "load_resource_cache",
        lambda scope, require_complete_coverage=False: [],
    )

    chicago = client.get("/api/site-advisor/tract-scores")
    county = client.get(
        "/api/site-advisor/tract-scores",
        params={"study_area": "cook_county"},
    )

    assert chicago.status_code == 200
    assert len(chicago.json()) == 1
    assert chicago.json()[0]["community_area"] == "North Lawndale"
    assert chicago.json()[0]["score_components"]["transit_burden"] == 70.0
    assert county.status_code == 200
    assert len(county.json()) == 2
    assert all(
        row["weights_used"]["transit_burden"] == 0 for row in county.json()
    )


def test_cook_county_rejects_nonzero_chicago_only_transit_weight(monkeypatch):
    response = client.get(
        "/api/site-advisor/tract-scores",
        params={"study_area": "cook_county", "transit_burden": 1},
    )

    assert response.status_code == 422
    assert "available only for Chicago" in response.json()["detail"]


def test_cook_county_accepts_explicit_zero_transit_weight(monkeypatch):
    monkeypatch.setattr(api_main, "get_all_tracts", prepared_urban_tracts)
    monkeypatch.setattr(
        api_main,
        "load_resource_cache",
        lambda scope, require_complete_coverage=False: [],
    )

    response = client.get(
        "/api/site-advisor/tract-scores",
        params={"study_area": "cook_county", "transit_burden": 0},
    )

    assert response.status_code == 200
    assert all(
        row["weights_used"]["transit_burden"] == 0 for row in response.json()
    )


def test_cook_county_accepts_partial_zero_component_override(monkeypatch):
    monkeypatch.setattr(api_main, "get_all_tracts", prepared_urban_tracts)
    monkeypatch.setattr(
        api_main,
        "load_resource_cache",
        lambda scope, require_complete_coverage=False: [],
    )

    response = client.get(
        "/api/site-advisor/tract-scores",
        params={"study_area": "cook_county", "poverty": 0},
    )

    assert response.status_code == 200
    assert all(row["weights_used"]["poverty"] == 0 for row in response.json())
    assert all(
        row["weights_used"]["transit_burden"] == 0 for row in response.json()
    )


def test_site_ranked_tracts_rejects_all_zero_weights(monkeypatch):
    response = client.get("/api/site-advisor/ranked-tracts", params={
        "food_access_gap": 0, "poverty": 0, "no_vehicle": 0,
        "population_served": 0, "transit_burden": 0, "existing_coverage": 0})
    assert response.status_code == 422
    assert "greater than zero" in response.json()["detail"]


def test_route_tract_scores_returns_every_prepared_rural_tract(monkeypatch):
    rural_tracts = [
        {
            "tract_fips": f"17091010{index:03d}",
            "population": 1000 + index,
            "low_access_half_mile": 0,
            "low_access_one_mile": 0,
            "centroid_lat": 41.1 + index / 1000,
            "centroid_lon": -87.9,
            "low_income_low_access_share": index / 100,
            "data_mode": "real",
        }
        for index in range(4)
    ]
    monkeypatch.setattr(api_main, "get_all_rural_tracts", lambda: rural_tracts)
    monkeypatch.setattr(
        api_main,
        "load_resource_cache",
        lambda scope, require_complete_coverage=False: [],
    )

    response = client.get("/api/route-advisor/tract-scores")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == len(rural_tracts)
    assert {row["tract_fips"] for row in body} == {
        tract["tract_fips"] for tract in rural_tracts
    }


def test_route_ranked_tracts_returns_scored_list(monkeypatch):
    monkeypatch.setattr(api_main, "load_resource_cache", lambda scope: [])
    monkeypatch.setattr(
        api_main,
        "get_low_access_rural_tracts",
        lambda: [{
            "tract_fips": "17089960100",
            "population": 1000,
            "low_access_half_mile": 1,
            "low_access_one_mile": 1,
            "centroid_lat": 41.88,
            "centroid_lon": -88.47,
            "data_mode": "real",
        }],
    )

    response = client.get("/api/route-advisor/ranked-tracts")

    assert response.status_code == 200
    body = response.json()
    assert len(body) > 0
    assert "need_score" in body[0]


def test_site_ranked_tracts_surfaces_missing_cache_as_503(monkeypatch):
    def _raise(_scope, **_kwargs):
        raise api_main.ResourceCacheError("cache unavailable")

    monkeypatch.setattr(api_main, "load_resource_cache", _raise)

    response = client.get("/api/site-advisor/ranked-tracts")

    assert response.status_code == 503


def test_site_resources_returns_list(monkeypatch):
    monkeypatch.setattr(
        api_main, "load_resource_cache", lambda scope: [{"kind": "grocery", "name": "Test", "lat": 41.8, "lon": -87.6}]
    )

    response = client.get("/api/site-advisor/resources")

    assert response.status_code == 200
    assert response.json() == [{"kind": "grocery", "name": "Test", "lat": 41.8, "lon": -87.6}]


def test_route_resources_surfaces_missing_cache_as_503(monkeypatch):
    def _raise(_scope, **_kwargs):
        raise api_main.ResourceCacheError("boom")

    monkeypatch.setattr(api_main, "load_resource_cache", _raise)

    response = client.get("/api/route-advisor/resources")

    assert response.status_code == 503


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


def test_route_optimization_endpoint_uses_road_network_provider(monkeypatch):
    monkeypatch.setattr(api_main, "get_road_route_matrix", lambda points, provider_name=None: [[0, 5], [5, 0]])
    response = client.post("/api/route-advisor/optimize", json={
        "depot": {"lat": 37.0, "lon": -89.2}, "max_route_minutes": 120,
        "vehicle_capacity": 10, "max_stops": 1,
        "candidates": [{"stop_id": "A", "lat": 37.01, "lon": -89.2, "demand": 5, "need_score": 90}],
    })
    assert response.status_code == 200
    assert response.json()["travel_time_source"] == "road_network_matrix"


def test_route_optimization_endpoint_surfaces_road_network_failure(monkeypatch):
    def fail(_points, provider_name=None):
        raise api_main.TravelTimeProviderError("routing unavailable")
    monkeypatch.setattr(api_main, "get_road_route_matrix", fail)
    response = client.post("/api/route-advisor/optimize", json={
        "depot": {"lat": 37.0, "lon": -89.2}, "max_route_minutes": 120,
        "vehicle_capacity": 10, "max_stops": 1,
        "candidates": [{"stop_id": "A", "lat": 37.01, "lon": -89.2, "demand": 5, "need_score": 90}],
    })
    assert response.status_code == 502


def test_route_directions_returns_road_geometry(monkeypatch):
    monkeypatch.setattr(api_main, "get_road_route_directions", lambda points, alternatives=2: {
        "coordinates": [[-89.2, 37.0], [-89.1, 37.1]],
        "legs": [], "distanceMiles": 8.5, "durationMinutes": 10, "alternatives": [],
    })
    response = client.post("/api/route-advisor/directions", json={
        "origin": {"lat": 37.0, "lon": -89.2},
        "waypoints": [],
        "destination": {"lat": 37.1, "lon": -89.1},
        "alternatives": 2,
    })
    assert response.status_code == 200
    assert len(response.json()["coordinates"]) == 2


def test_site_evidence_returns_brief_text(monkeypatch):
    monkeypatch.setattr(
        api_main,
        "write_site_evidence_brief",
        lambda tract: "This tract has high need...",
    )

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
        json={"tract": {"tract_fips": "17089960100", "need_score": 80.0}},
    )

    assert response.status_code == 502


def test_verify_updates_status(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOD_ACCESS_AUTH_MODE", "disabled")
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
    monkeypatch.setenv("FOOD_ACCESS_AUTH_MODE", "disabled")
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
    monkeypatch.setenv("FOOD_ACCESS_AUTH_MODE", "disabled")
    use_temp_db(tmp_path, monkeypatch)

    response = client.post(
        "/api/flagged-tracts/verify",
        json={"tract_fips": "00000000000", "recommendation_type": "site", "verification": "verified_open"},
    )

    assert response.status_code == 400


def test_verify_requires_staff_sign_in(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOD_ACCESS_AUTH_MODE", "required")
    response = client.post(
        "/api/flagged-tracts/verify",
        json={"tract_fips": "17031840000", "recommendation_type": "site", "verification": "verified_open"},
    )
    assert response.status_code == 401
    assert "staff account" in response.json()["detail"]


def test_evidence_review_records_human_action(monkeypatch):
    monkeypatch.setenv("FOOD_ACCESS_AUTH_MODE", "disabled")
    monkeypatch.setattr(api_main, "review_change", lambda **kwargs: {
        **kwargs, "source_id": "markets", "scope": "urban", "review_status": kwargs["action"],
    })
    response = client.post(
        "/api/watchdog/changes/review",
        json={
            "source_scope": "markets#urban", "record_key": "CHANGE#1",
            "action": "acknowledged", "note": "Reviewed the source outage",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "review_recorded"
    assert "No site ranking or route" in response.json()["message"]


def test_impact_metrics_returns_both_regions(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17089960100", recommendation_type="route", source_agent="route_advisor"
    )

    response = client.get("/api/impact-metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["urban"]["total_flagged"] == 1
    assert body["rural"]["total_flagged"] == 1


def test_watchdog_changes_excludes_non_community_history(monkeypatch):
    captured = {}

    def read_page(**kwargs):
        captured.update(kwargs)
        return {
            "items": [],
            "next_cursor": None,
            "page_size": 0,
            "open_finding_count": 0,
            "source_count": 0,
            "findings_window_truncated": False,
        }

    monkeypatch.setattr(api_main, "read_change_page", read_page)

    response = client.get("/api/watchdog/changes")

    assert response.status_code == 200
    assert captured["excluded_source_ids"] == frozenset({"cta_gtfs"})
    assert captured["excluded_source_scopes"] == frozenset({
        "chicago_farmers_markets#urban"
    })
