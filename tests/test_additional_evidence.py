from datetime import datetime, timezone

from data_sources.contracts import DataQualityReport, EvidenceStatus, ResourceEvidenceBatch, SourceCitation
import tools.additional_evidence as additional_evidence
from tools.additional_evidence import refresh_additional_sources


def batch(source_id, status=EvidenceStatus.COMPLETE):
    citation = SourceCitation(source_name="Official", dataset_name=source_id, official_url="https://example.org",
        vintage="2026", retrieved_at=datetime(2026, 8, 29, tzinfo=timezone.utc), geographic_level="point")
    return ResourceEvidenceBatch(source_id=source_id, records=[], citation=citation,
        quality=DataQualityReport(status=status))


class Socrata:
    def fetch_food_inspections(self): return batch("chicago_food_inspections")
    def fetch_active_food_businesses(self): raise RuntimeError("rate limited")
    def fetch_farmers_markets(self): return batch("chicago_farmers_markets", EvidenceStatus.STALE_CACHE)


class GTFS:
    def fetch_stops(self): return batch("cta_gtfs")


def test_refresh_isolates_failures_and_maps_stale_status(monkeypatch):
    monkeypatch.delenv("EVENTBRITE_API_TOKEN", raising=False)
    monkeypatch.delenv("EVENTBRITE_ORGANIZATION_ID", raising=False)
    calls = []
    def snapshot(source_id, records, **kwargs):
        calls.append((source_id, kwargs["status"]))
        return {"source_id": source_id, "status": kwargs["status"]}
    results = refresh_additional_sources(socrata=Socrata(), gtfs=GTFS(), snapshot_fn=snapshot)
    assert len(results) == 4
    assert ("chicago_active_business_licenses", "failed") in calls
    assert ("chicago_farmers_markets", "stale") in calls


def test_refresh_includes_eventbrite_when_server_settings_are_present(monkeypatch):
    monkeypatch.setenv("EVENTBRITE_API_TOKEN", "private-token")
    monkeypatch.setenv("EVENTBRITE_ORGANIZATION_ID", "77")
    monkeypatch.setattr(
        additional_evidence,
        "refresh_eventbrite_events",
        lambda **_kwargs: {"source_id": "eventbrite_community_events", "status": "complete"},
    )
    results = refresh_additional_sources(socrata=Socrata(), gtfs=GTFS(), snapshot_fn=lambda *args, **kwargs: {
        "source_id": args[0], "status": kwargs["status"],
    })
    assert len(results) == 5
    assert results[-1]["source_id"] == "eventbrite_community_events"
