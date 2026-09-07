"""Application service connecting Eventbrite events to Watchdog evidence."""

from __future__ import annotations

import os
from typing import Any, Callable

from data_sources.eventbrite import (
    EventbriteClient,
    build_event_batch,
    event_id_from_webhook,
    normalize_event,
)
from tools.evidence_snapshots import record_snapshot


def _client_from_environment() -> EventbriteClient:
    token = os.getenv("EVENTBRITE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("EVENTBRITE_API_TOKEN is not configured")
    return EventbriteClient(token)


def refresh_eventbrite_events(*, client: EventbriteClient | None = None,
                               organization_id: str | None = None,
                               snapshot_fn: Callable = record_snapshot) -> dict[str, Any]:
    client = client or _client_from_environment()
    org_id = (organization_id or os.getenv("EVENTBRITE_ORGANIZATION_ID", "")).strip()
    if not org_id:
        raise RuntimeError("EVENTBRITE_ORGANIZATION_ID is not configured")
    batch = build_event_batch(client.iter_organization_events(org_id))
    records = [record.model_dump(mode="json") for record in batch.records]
    result = snapshot_fn(batch.source_id, records, scope="chicago", status="complete")
    return {
        **result,
        "source_row_count": batch.quality.source_row_count,
        "matched_event_count": batch.quality.matched_rows,
        "excluded_event_count": batch.quality.excluded_rows,
        "message": "Eventbrite events refreshed. No ranking, route, or dispatch changed automatically.",
    }


def ingest_eventbrite_webhook(payload: dict[str, Any], *, client: EventbriteClient | None = None,
                              snapshot_fn: Callable = record_snapshot) -> dict[str, Any]:
    """Re-fetch and persist a webhook event; never trust webhook fields directly."""
    event_id = event_id_from_webhook(payload)
    client = client or _client_from_environment()
    event = client.fetch_event(event_id)
    record = normalize_event(event)
    if not record.attributes.get("relevant"):
        return {
            "status": "ignored",
            "event_id": event_id,
            "reason": "Event is outside the configured Chicago food-access scope.",
        }
    result = snapshot_fn(
        "eventbrite_community_events",
        [record.model_dump(mode="json")],
        scope=f"chicago:event:{event_id}",
        status="complete",
    )
    return {
        "status": "accepted",
        "event_id": event_id,
        "snapshot": result,
        "message": "Event change recorded for human review. No ranking, route, or dispatch changed automatically.",
    }
