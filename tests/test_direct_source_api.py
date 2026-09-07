from fastapi.testclient import TestClient

import api.main as api_main


client = TestClient(api_main.app)


def test_source_registry_is_public_and_contains_official_links():
    response = client.get("/api/community-signals/sources")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 7
    assert all(item["url"].startswith("https://") for item in payload)


def test_manual_refresh_is_staff_only_and_never_changes_plan(monkeypatch):
    monkeypatch.setenv("FOOD_ACCESS_AUTH_MODE", "disabled")
    monkeypatch.setattr(
        api_main,
        "refresh_direct_sources",
        lambda: {
            "source_count": 7,
            "finding_count": 2,
            "message": "Official community sources refreshed. No ranking, route, mission, or dispatch changed automatically.",
        },
    )
    response = client.post("/api/community-signals/refresh")
    assert response.status_code == 200
    assert response.json()["source_count"] == 7
    assert "No ranking, route, mission, or dispatch changed automatically" in response.json()["message"]
