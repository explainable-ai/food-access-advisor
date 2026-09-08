"""Refresh official supplemental sources and hand each result to the Watchdog."""

import os
from typing import Callable

from data_sources.chicago_socrata import ChicagoSocrataClient
from data_sources.cta_gtfs import CTAGTFSClient
from data_sources.contracts import ResourceEvidenceBatch
from tools.evidence_snapshots import record_snapshot


def refresh_direct_sources(*args, **kwargs):
    from services.direct_source_signals import refresh_direct_sources as _refresh_direct_sources

    return _refresh_direct_sources(*args, **kwargs)


def _snapshot_batch(batch: ResourceEvidenceBatch, snapshot_fn: Callable = record_snapshot) -> dict:
    status = "stale" if batch.quality.status.value == "stale_cache" else batch.quality.status.value
    records = [record.model_dump(mode="json") for record in batch.records]
    return snapshot_fn(batch.source_id, records, scope="urban", status=status)


def refresh_additional_sources(*, socrata: ChicagoSocrataClient | None = None,
                               gtfs: CTAGTFSClient | None = None,
                               snapshot_fn: Callable = record_snapshot,
                               include_direct_sources: bool = True,
                               include_transit: bool = True,
                               include_legacy_farmers_markets: bool = True) -> list[dict]:
    """Fetch independent sources, recording failures without aborting siblings.

    Transit and the legacy Socrata farmers-market monitor remain available to
    non-community callers. The Community Access Watch scheduler disables both
    so its queue stays focused on food-access changes and the approved direct
    farmers-market source is not duplicated.
    """
    socrata = socrata or ChicagoSocrataClient(app_token=os.getenv("SOCRATA_APP_TOKEN") or None)
    sources = [
        ("chicago_food_inspections", socrata.fetch_food_inspections),
        ("chicago_active_business_licenses", socrata.fetch_active_food_businesses),
    ]
    if include_legacy_farmers_markets:
        sources.append(("chicago_farmers_markets", socrata.fetch_farmers_markets))
    if include_transit:
        gtfs = gtfs or CTAGTFSClient()
        sources.append(("cta_gtfs", gtfs.fetch_stops))
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
