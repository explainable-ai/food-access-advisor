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


def test_refresh_isolates_failures_and_maps_stale_status():
    calls = []
    def snapshot(source_id, records, **kwargs):
        calls.append((source_id, kwargs["status"]))
        return {"source_id": source_id, "status": kwargs["status"]}
    results = refresh_additional_sources(
        socrata=Socrata(), gtfs=GTFS(), snapshot_fn=snapshot,
        include_direct_sources=False,
    )
    assert len(results) == 4
    assert ("chicago_active_business_licenses", "failed") in calls
    assert ("chicago_farmers_markets", "stale") in calls


def test_scheduled_refresh_includes_approved_direct_sources(monkeypatch):
    monkeypatch.setattr(
        additional_evidence,
        "refresh_direct_sources",
        lambda **_kwargs: {"sources": [
            {"source_id": "fresh_moves_mobile_market", "status": "complete"}
        ]},
    )
    results = refresh_additional_sources(
        socrata=Socrata(),
        gtfs=GTFS(),
        snapshot_fn=lambda *args, **kwargs: {
            "source_id": args[0], "status": kwargs["status"],
        },
    )
    assert len(results) == 5
    assert results[-1]["source_id"] == "fresh_moves_mobile_market"


def test_community_watch_can_skip_transit_and_legacy_market_source(monkeypatch):
    monkeypatch.setattr(
        additional_evidence,
        "refresh_direct_sources",
        lambda **_kwargs: {"sources": [{
            "source_id": "fresh_moves_mobile_market", "status": "complete"
        }]},
    )
    results = refresh_additional_sources(
        socrata=Socrata(),
        gtfs=GTFS(),
        snapshot_fn=lambda *args, **kwargs: {
            "source_id": args[0], "status": kwargs["status"],
        },
        include_transit=False,
        include_legacy_farmers_markets=False,
    )

    source_ids = {result["source_id"] for result in results}
    assert "cta_gtfs" not in source_ids
    assert "chicago_farmers_markets" not in source_ids
    assert source_ids == {
        "chicago_food_inspections",
        "chicago_active_business_licenses",
        "fresh_moves_mobile_market",
    }
