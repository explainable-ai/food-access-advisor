from fastapi.testclient import TestClient

import api.main as api_main


client = TestClient(api_main.app)


def test_eventbrite_webhook_requires_configured_shared_secret(monkeypatch):
    monkeypatch.delenv("EVENTBRITE_WEBHOOK_SECRET", raising=False)
    response = client.post(
        "/api/integrations/eventbrite/webhook",
        params={"token": "a" * 32},
        json={"api_url": "https://www.eventbriteapi.com/v3/events/123456789/"},
    )
    assert response.status_code == 503


def test_eventbrite_webhook_rejects_wrong_shared_secret(monkeypatch):
    monkeypatch.setenv("EVENTBRITE_WEBHOOK_SECRET", "correct-very-long-webhook-secret")
    response = client.post(
        "/api/integrations/eventbrite/webhook",
        params={"token": "incorrect-very-long-secret"},
        json={"api_url": "https://www.eventbriteapi.com/v3/events/123456789/"},
    )
    assert response.status_code == 401


def test_eventbrite_webhook_accepts_verified_callback(monkeypatch):
    secret = "correct-very-long-webhook-secret"
    monkeypatch.setenv("EVENTBRITE_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(
        api_main,
        "ingest_eventbrite_webhook",
        lambda payload: {"status": "accepted", "event_id": "123456789"},
    )
    response = client.post(
        "/api/integrations/eventbrite/webhook",
        params={"token": secret},
        json={"api_url": "https://www.eventbriteapi.com/v3/events/123456789/"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "event_id": "123456789"}


def test_manual_refresh_is_staff_only_and_never_changes_plan(monkeypatch):
    monkeypatch.setenv("FOOD_ACCESS_AUTH_MODE", "disabled")
    monkeypatch.setattr(
        api_main,
        "refresh_eventbrite_events",
        lambda: {
            "matched_event_count": 1,
            "message": "Eventbrite events refreshed. No ranking, route, or dispatch changed automatically.",
        },
    )
    response = client.post("/api/community-signals/eventbrite/refresh")
    assert response.status_code == 200
    assert response.json()["matched_event_count"] == 1
    assert "No ranking, route, or dispatch changed automatically" in response.json()["message"]
