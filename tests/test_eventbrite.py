from datetime import datetime, timezone

import pytest

from data_sources.eventbrite import (
    build_event_batch,
    event_id_from_webhook,
    relevance_evidence,
)
from services.eventbrite_signals import ingest_eventbrite_webhook, refresh_eventbrite_events


def event(**overrides):
    value = {
        "id": "123456789",
        "name": {"text": "Riverdale Free Groceries Pop-Up"},
        "summary": "A mobile pantry for Chicago families.",
        "description": {"text": "Community food distribution"},
        "status": "live",
        "url": "https://www.eventbrite.com/e/123456789",
        "changed": "2026-09-07T12:00:00Z",
        "start": {"utc": "2026-09-10T15:00:00Z"},
        "end": {"utc": "2026-09-10T18:00:00Z"},
        "category_id": "111",
        "format_id": "115",
        "venue": {
            "id": "44",
            "name": "Riverdale Community Center",
            "latitude": "41.65",
            "longitude": "-87.63",
            "address": {"city": "Chicago", "region": "IL", "localized_address_display": "Chicago, IL"},
        },
        "organization": {"id": "77", "name": "Demo Food Network"},
        "ticket_availability": {"has_available_tickets": True},
    }
    value.update(overrides)
    return value


class Client:
    def __init__(self, events): self.events = events
    def iter_organization_events(self, _organization_id): return iter(self.events)
    def fetch_event(self, _event_id): return self.events[0]


def test_semantic_keyword_category_and_format_evidence():
    result = relevance_evidence(event())
    assert result["relevant"] is True
    assert "free groceries" in result["matched_keywords"]["direct_assistance"]
    assert result["matched_category"] == "Charities & Causes"
    assert result["matched_format"] == "Pop-Up / Street Market"


def test_broad_category_alone_does_not_create_food_finding():
    result = relevance_evidence(event(
        name={"text": "Neighborhood Art Meetup"},
        summary="Creative gathering",
        description={"text": "Painting workshop"},
        format_id="",
    ))
    assert result["relevant"] is False


def test_event_batch_keeps_only_relevant_chicago_events():
    unrelated = event(id="222", name={"text": "Tech Conference"}, summary="Software", description={"text": "Coding"},
                      category_id="102", format_id="102")
    batch = build_event_batch([event(), unrelated], retrieved_at=datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert batch.quality.source_row_count == 2
    assert batch.quality.matched_rows == 1
    assert batch.records[0].entity_id == "eventbrite:123456789"


def test_webhook_rejects_untrusted_api_url():
    with pytest.raises(ValueError, match="not an Eventbrite"):
        event_id_from_webhook({"api_url": "https://evil.example/v3/events/123456789/"})


def test_webhook_refetches_event_and_records_human_review_finding():
    calls = []
    result = ingest_eventbrite_webhook(
        {"api_url": "https://www.eventbriteapi.com/v3/events/123456789/"},
        client=Client([event()]),
        snapshot_fn=lambda source_id, records, **kwargs: calls.append((source_id, records, kwargs)) or {"changes": []},
    )
    assert result["status"] == "accepted"
    assert calls[0][0] == "eventbrite_community_events"
    assert calls[0][2]["scope"] == "chicago:event:123456789"
    assert "No ranking, route, or dispatch changed automatically" in result["message"]


def test_refresh_applies_local_discovery_and_records_snapshot():
    calls = []
    result = refresh_eventbrite_events(
        client=Client([event()]),
        organization_id="77",
        snapshot_fn=lambda source_id, records, **kwargs: calls.append((source_id, records, kwargs))
        or {"source_id": source_id, "record_count": len(records)},
    )
    assert result["record_count"] == 1
    assert result["matched_event_count"] == 1
    assert result["baselined_event_count"] == 1
    assert [call[2]["scope"] for call in calls] == ["chicago", "chicago:event:123456789"]
