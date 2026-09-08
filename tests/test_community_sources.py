from datetime import datetime, timezone

import pytest

from data_sources.community_sources import (
    APPROVED_SOURCES,
    CommunitySource,
    CommunitySourceClient,
    CommunitySourceError,
    keyword_matches,
    parse_source_html,
    source_registry,
)
from services.direct_source_signals import refresh_direct_sources


SOURCE = CommunitySource(
    "test_food_events",
    "Official Food Partner",
    "https://www.chicagosfoodbank.org/find-food-2/",
    "chicago",
    "food_assistance",
    "Official food-assistance schedules.",
)


def test_keyword_groups_preserve_discovery_criteria():
    matches = keyword_matches(
        "Free groceries at our mobile pantry plus a food justice community garden workshop"
    )
    assert matches["direct_assistance"] == ["free groceries", "mobile pantry"]
    assert matches["food_sovereignty_and_justice"] == ["food justice", "community garden"]


def test_json_ld_event_is_normalized_with_official_provenance():
    html = '''
    <script type="application/ld+json">
    {
      "@context": "https://schema.org", "@type": "Event",
      "@id": "food-drive-9", "name": "Riverdale Free Groceries",
      "description": "Mobile pantry for Chicago families",
      "startDate": "2026-09-10T10:00:00-05:00",
      "location": {"@type": "Place", "name": "Community Center",
        "address": {"streetAddress": "1 Main St", "addressLocality": "Chicago", "addressRegion": "IL"},
        "geo": {"latitude": 41.65, "longitude": -87.63}},
      "url": "https://www.chicagosfoodbank.org/events/food-drive-9"
    }
    </script>
    '''
    batch = parse_source_html(SOURCE, html, retrieved_at=datetime(2026, 9, 8, tzinfo=timezone.utc))
    assert batch.quality.status.value == "complete"
    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.name == "Riverdale Free Groceries"
    assert record.lat == 41.65
    assert record.source_citation.source_name == "Official Food Partner"
    assert record.attributes["official_source"] is True


def test_keyword_only_navigation_is_not_a_finding():
    html = """
    <nav><a>Find Food</a><a>Donate Food</a><a>Volunteer</a></nav>
    <p>Free groceries and mobile pantry programs.</p>
    """
    batch = parse_source_html(SOURCE, html)
    assert batch.quality.status.value == "partial"
    assert batch.records == []


def test_fresh_moves_requires_a_schedule_time_and_address():
    source = next(item for item in APPROVED_SOURCES if item.source_id == "fresh_moves_mobile_market")
    html = """
    <h2>Regular Mobile Market Schedule</h2>
    <h3>Mondays</h3>
    <p>10:00 AM to 11:30 AM: Roosevelt Square Farm, 1242 S Loomis St.</p>
    <p>1:00 PM to 3:00 PM: Thresholds Austin, 334 N Menard Ave.</p>
    <h2>What's On The Bus</h2>
    """
    batch = parse_source_html(source, html)
    assert batch.quality.status.value == "complete"
    assert len(batch.records) == 2
    assert all(record.attributes["start_at"] for record in batch.records)
    assert {record.attributes["location"] for record in batch.records} == {
        "1242 S Loomis St", "334 N Menard Ave"
    }


def test_fresh_moves_accepts_live_schedule_formats():
    source = next(item for item in APPROVED_SOURCES if item.source_id == "fresh_moves_mobile_market")
    html = """
    <h2>Regular Mobile Market Schedule</h2>
    <h3>EVERY OTHER WEDNESDAY: 08/12, 08/26, 09/09, 09/23</h3>
    <p>4:30-6:00 Boxville, 330 E 51st St</p>
    <h3>THURSDAYS:</h3>
    <p>10:30 AM - 12:00 PM: Trina Davila, 4300 W North Ave</p>
    <h3>MONDAYS:</h3>
    <p>1:00 PM - 3:00 PM: Thresholds Austin, 334 N Menard</p>
    <h2>What's On The Bus</h2>
    """
    batch = parse_source_html(source, html)
    assert batch.quality.status.value == "complete"
    assert len(batch.records) == 3
    assert {record.attributes["location"] for record in batch.records} == {
        "330 E 51st St", "4300 W North Ave", "334 N Menard"
    }
    assert any("4:30-6:00" in record.attributes["start_at"] for record in batch.records)


def test_beyond_hunger_requires_a_dated_upcoming_event():
    source = next(item for item in APPROVED_SOURCES if item.source_id == "beyond_hunger_events")
    html = """
    <h2>Upcoming Events</h2>
    <h3>Children's Storytime</h3>
    <p>September 19, 2026, 10:30 AM to 11:30 AM</p>
    <p>A family event supporting Beyond Hunger.</p>
    <h2>Past Events</h2>
    """
    batch = parse_source_html(source, html)
    assert batch.quality.status.value == "complete"
    assert [record.name for record in batch.records] == ["Children's Storytime"]


def test_empty_or_redesigned_page_is_partial_not_mass_removal():
    batch = parse_source_html(SOURCE, "<html><body><p>Temporarily unavailable.</p></body></html>")
    assert batch.quality.status.value == "partial"
    assert batch.records == []
    assert "previous evidence must remain active" in batch.quality.warnings[0]


def test_known_first_party_empty_state_is_healthy_zero_records():
    source = CommunitySource(
        "official_events", "Official Events", SOURCE.url, "chicago",
        "community_event", "Official events.", ("No upcoming events",),
    )
    batch = parse_source_html(source, "<h2>No upcoming events</h2><p>Check back soon.</p>")
    assert batch.quality.status.value == "complete"
    assert batch.records == []
    assert batch.quality.warnings == []


class Response:
    def __init__(self, *, url=SOURCE.url, content_type="text/html", body="<p>Free groceries today.</p>"):
        self.url = url
        self.headers = {"Content-Type": content_type}
        self.content = body.encode()
        self.text = body

    def raise_for_status(self):
        return None


class Session:
    def __init__(self, response):
        self.response = response

    def get(self, *_args, **_kwargs):
        return self.response


def test_client_rejects_redirect_outside_approved_hosts():
    client = CommunitySourceClient(session=Session(Response(url="https://evil.example/events")))
    with pytest.raises(CommunitySourceError, match="redirected outside"):
        client.fetch(SOURCE)


def test_registry_contains_only_https_approved_sources():
    registry = source_registry()
    assert len(registry) == 7
    assert all(item["url"].startswith("https://") for item in registry)
    assert {item["source_id"] for item in registry} == {item.source_id for item in APPROVED_SOURCES}


class BatchClient:
    def fetch(self, source):
        return parse_source_html(source, """
        <script type="application/ld+json">
        {"@type":"Event","@id":"food-drive-1","name":"Free Grocery Food Drive",
        "description":"Mobile pantry for Chicago families","startDate":"2026-09-10T10:00:00-05:00"}
        </script>
        """)


def test_refresh_isolates_sources_and_never_changes_plans():
    calls = []

    def snapshot(source_id, records, **kwargs):
        calls.append((source_id, records, kwargs))
        return {"source_id": source_id, "status": kwargs["status"], "changes": []}

    result = refresh_direct_sources(
        client=BatchClient(), sources=(SOURCE,), include_food_equity=False,
        snapshot_fn=snapshot,
    )
    assert result["status"] == "complete"
    assert result["healthy_source_count"] == 1
    assert calls[0][2]["min_retained_fraction"] == 0.60
    assert "No ranking, route, mission, or dispatch changed automatically" in result["message"]
