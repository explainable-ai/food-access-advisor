from datetime import datetime, timezone

from data_sources.contracts import DataQualityReport, EvidenceStatus, ResourceEvidenceBatch, SourceCitation
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


def test_refresh_isolates_failures_and_maps_stale_status():
    calls = []
    def snapshot(source_id, records, **kwargs):
        calls.append((source_id, kwargs["status"]))
        return {"source_id": source_id, "status": kwargs["status"]}
    results = refresh_additional_sources(socrata=Socrata(), snapshot_fn=snapshot)
    assert len(results) == 3
    assert ("chicago_active_business_licenses", "failed") in calls
    assert ("chicago_farmers_markets", "stale") in calls
    assert all(source_id != "cta_gtfs" for source_id, _status in calls)
