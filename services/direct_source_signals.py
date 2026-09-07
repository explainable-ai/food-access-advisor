"""Application service connecting approved public sources to Watchdog."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from data_sources.community_sources import (
    APPROVED_SOURCES,
    CommunitySource,
    CommunitySourceClient,
)
from tools.evidence_snapshots import record_snapshot


def refresh_direct_sources(
    *,
    client: CommunitySourceClient | None = None,
    sources: Iterable[CommunitySource] = APPROVED_SOURCES,
    snapshot_fn: Callable = record_snapshot,
) -> dict[str, Any]:
    """Refresh every source independently and retain prior evidence on failure."""
    client = client or CommunitySourceClient()
    results: list[dict[str, Any]] = []
    for source in sources:
        try:
            batch = client.fetch(source)
            records = [record.model_dump(mode="json") for record in batch.records]
            status = "complete" if batch.quality.status.value == "complete" else "partial"
            warning = "; ".join(batch.quality.warnings) or None
            snapshot = snapshot_fn(
                batch.source_id,
                records,
                scope=source.scope,
                status=status,
                error=warning,
                min_retained_fraction=0.60,
                min_baseline_records=3,
            )
            results.append({
                **snapshot,
                "source_name": source.name,
                "official_url": source.url,
                "matched_record_count": len(records),
            })
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
