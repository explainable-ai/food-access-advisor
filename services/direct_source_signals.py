"""Application service connecting approved public sources to Sentry."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from data_sources.chicago_food_equity import (
    DASHBOARD_URL,
    SOURCE_ID as FOOD_EQUITY_SOURCE_ID,
    SOURCE_NAME as FOOD_EQUITY_SOURCE_NAME,
    ChicagoFoodEquityClient,
)
from data_sources.community_sources import (
    APPROVED_SOURCES,
    CommunitySource,
    CommunitySourceClient,
)
from tools.evidence_snapshots import record_snapshot


def _snapshot_batch(
    *,
    source_id: str,
    source_name: str,
    official_url: str,
    scope: str,
    batch,
    snapshot_fn: Callable,
    min_retained_fraction: float,
    min_baseline_records: int,
) -> dict[str, Any]:
    records = [record.model_dump(mode="json") for record in batch.records]
    status = "complete" if batch.quality.status.value == "complete" else "partial"
    warning = "; ".join(batch.quality.warnings) or None
    snapshot = snapshot_fn(
        source_id,
        records,
        scope=scope,
        status=status,
        error=warning,
        min_retained_fraction=min_retained_fraction,
        min_baseline_records=min_baseline_records,
    )
    return {
        **snapshot,
        "source_name": source_name,
        "official_url": official_url,
        "matched_record_count": len(records),
    }


def refresh_direct_sources(
    *,
    client: CommunitySourceClient | None = None,
    sources: Iterable[CommunitySource] = APPROVED_SOURCES,
    food_equity_client: ChicagoFoodEquityClient | None = None,
    include_food_equity: bool = True,
    snapshot_fn: Callable = record_snapshot,
) -> dict[str, Any]:
    """Refresh every source independently and retain prior evidence on failure."""
    client = client or CommunitySourceClient()
    results: list[dict[str, Any]] = []

    if include_food_equity:
        food_equity_client = food_equity_client or ChicagoFoodEquityClient()
        try:
            batch = food_equity_client.fetch()
            results.append(_snapshot_batch(
                source_id=FOOD_EQUITY_SOURCE_ID,
                source_name=FOOD_EQUITY_SOURCE_NAME,
                official_url=DASHBOARD_URL,
                scope="chicago",
                batch=batch,
                snapshot_fn=snapshot_fn,
                min_retained_fraction=0.85,
                min_baseline_records=100,
            ))
        except Exception as exc:
            snapshot = snapshot_fn(
                FOOD_EQUITY_SOURCE_ID,
                [],
                scope="chicago",
                status="failed",
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
            results.append({
                **snapshot,
                "source_name": FOOD_EQUITY_SOURCE_NAME,
                "official_url": DASHBOARD_URL,
                "matched_record_count": 0,
            })

    for source in sources:
        try:
            batch = client.fetch(source)
            results.append(_snapshot_batch(
                source_id=batch.source_id,
                source_name=source.name,
                official_url=source.url,
                scope=source.scope,
                batch=batch,
                snapshot_fn=snapshot_fn,
                min_retained_fraction=0.60,
                min_baseline_records=3,
            ))
        except Exception as exc:
            snapshot = snapshot_fn(
                source.source_id,
                [],
                scope=source.scope,
                status="failed",
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
            results.append({
                **snapshot,
                "source_name": source.name,
                "official_url": source.url,
                "matched_record_count": 0,
            })
    return {
        "status": "complete" if all(item.get("status") == "complete" for item in results) else "partial",
        "source_count": len(results),
        "healthy_source_count": sum(item.get("status") == "complete" for item in results),
        "finding_count": sum(len(item.get("changes") or []) for item in results),
        "sources": results,
        "message": "Official community sources refreshed. No ranking, route, mission, or dispatch changed automatically.",
    }
