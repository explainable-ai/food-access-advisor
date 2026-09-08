"""Refresh official supplemental sources and hand each result to the Watchdog."""

import os
from typing import Callable

from data_sources.chicago_socrata import ChicagoSocrataClient
from data_sources.cta_gtfs import CTAGTFSClient
from data_sources.contracts import ResourceEvidenceBatch
from services.direct_source_signals import refresh_direct_sources
from tools.evidence_snapshots import record_snapshot


def _snapshot_batch(batch: ResourceEvidenceBatch, snapshot_fn: Callable = record_snapshot) -> dict:
    status = "stale" if batch.quality.status.value == "stale_cache" else batch.quality.status.value
    records = [record.model_dump(mode="json") for record in batch.records]
    return snapshot_fn(batch.source_id, records, scope="urban", status=status)


def refresh_additional_sources(*, socrata: ChicagoSocrataClient | None = None,
                               gtfs: CTAGTFSClient | None = None,
                               snapshot_fn: Callable = record_snapshot,
                               include_direct_sources: bool = True) -> list[dict]:
    """Fetch independent sources, recording failures without aborting siblings."""
    socrata = socrata or ChicagoSocrataClient(app_token=os.getenv("SOCRATA_APP_TOKEN") or None)
    gtfs = gtfs or CTAGTFSClient()
    sources = [
        ("chicago_food_inspections", socrata.fetch_food_inspections),
        ("chicago_active_business_licenses", socrata.fetch_active_food_businesses),
        ("chicago_farmers_markets", socrata.fetch_farmers_markets),
        ("cta_gtfs", gtfs.fetch_stops),
    ]
    results = []
    for source_id, fetch in sources:
        try:
            results.append(_snapshot_batch(fetch(), snapshot_fn=snapshot_fn))
        except Exception as exc:
            results.append(snapshot_fn(source_id, [], scope="urban", status="failed",
                                       error=f"{type(exc).__name__}: {exc}"))

    if include_direct_sources:
        direct = refresh_direct_sources(snapshot_fn=snapshot_fn)
        results.extend(direct["sources"])
    return results
