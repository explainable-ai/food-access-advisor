"""Eventbrite organization-event ingestion for Community Access Watch.

Eventbrite retired its public Event Search API. This client therefore reads
events owned by an explicitly authorized organization and performs geographic
and semantic screening locally. The API token is server-side only.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

from data_sources.contracts import (
    DataQualityReport,
    EvidenceStatus,
    ResourceEvidence,
    ResourceEvidenceBatch,
    SourceCitation,
)

EVENTBRITE_API_BASE_URL = "https://www.eventbriteapi.com/v3"
EVENTBRITE_API_HOSTS = {"www.eventbriteapi.com", "eventbriteapi.com"}
EVENTBRITE_EXPAND = "venue,organization,ticket_availability"

SEMANTIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "direct_assistance": (
        "food distribution",
        "free groceries",
        "pop-up pantry",
        "popup pantry",
        "mobile market",
        "community fridge",
        "mobile pantry",
        "food drive",
    ),
    "food_sovereignty_and_justice": (
        "food justice",
        "urban farm",
        "community garden",
        "sustainable agriculture",
        "food desert",
        "mutual aid",
    ),
    "social_support_and_funding": (
        "community resources",
        "hunger relief",
        "volunteer orientation",
        "pantry fundraiser",
        "social service fair",
    ),
}

RELEVANT_CATEGORY_IDS = {
    "111": "Charities & Causes",
    "113": "Community & Culture",
}
RELEVANT_FORMAT_IDS = {"115": "Pop-Up / Street Market"}
CHICAGO_TERMS = (
    "chicago",
    "cook county",
    "riverdale",
    "roseland",
    "woodlawn",
    "englewood",
    "hegewisch",
    "illinois",
    " il ",
)


class EventbriteAPIError(RuntimeError):
    """Raised when Eventbrite cannot provide a usable response."""


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or value.get("html") or "")
    return str(value or "")


def _event_search_text(event: dict[str, Any]) -> str:
    venue = event.get("venue") or {}
    organization = event.get("organization") or {}
    address = venue.get("address") or {}
    parts = [
        _text(event.get("name")),
        _text(event.get("summary")),
        _text(event.get("description")),
        _text(organization.get("name")),
        _text(venue.get("name")),
        *[str(value or "") for value in address.values()],
    ]
    return " ".join(parts).casefold()


def semantic_matches(event: dict[str, Any]) -> dict[str, list[str]]:
    """Return every configured phrase found in the expanded event payload."""
    haystack = _event_search_text(event)
    return {
        group: [term for term in terms if term.casefold() in haystack]
        for group, terms in SEMANTIC_KEYWORDS.items()
        if any(term.casefold() in haystack for term in terms)
    }


def relevance_evidence(event: dict[str, Any]) -> dict[str, Any]:
    """Explain a deterministic food-access relevance decision.

    Categories and formats broaden recall, but a broad charity/community tag
    alone is not enough to classify an event as food-access related.
    """
    matches = semantic_matches(event)
    category_id = str(event.get("category_id") or "")
    format_id = str(event.get("format_id") or "")
    category_match = RELEVANT_CATEGORY_IDS.get(category_id)
    format_match = RELEVANT_FORMAT_IDS.get(format_id)
    chicago_match = any(term in f" {_event_search_text(event)} " for term in CHICAGO_TERMS)
    direct = bool(matches.get("direct_assistance"))
    semantic = bool(matches)
    relevant = chicago_match and (semantic or (category_match and format_match))
    score = min(
        100,
        (45 if direct else 25 if semantic else 0)
        + (15 if category_match else 0)
        + (15 if format_match else 0)
        + (20 if chicago_match else 0),
    )
    return {
        "relevant": relevant,
        "relevance_score": score,
        "matched_keywords": matches,
        "matched_category": category_match,
        "matched_format": format_match,
        "chicago_area_match": chicago_match,
    }


def _parse_event_time(value: Any) -> datetime | None:
    raw = value.get("utc") if isinstance(value, dict) else value
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_event(event: dict[str, Any], *, retrieved_at: datetime | None = None) -> ResourceEvidence:
    event_id = str(event.get("id") or "").strip()
    if not event_id:
        raise ValueError("Eventbrite event is missing an id")
    name = _text(event.get("name")).strip()
    if not name:
        raise ValueError(f"Eventbrite event {event_id} is missing a name")
    retrieved = retrieved_at or datetime.now(timezone.utc)
    venue = event.get("venue") or {}
    organization = event.get("organization") or {}
    address = venue.get("address") or {}
    evidence = relevance_evidence(event)
    start_at = _parse_event_time(event.get("start"))
    end_at = _parse_event_time(event.get("end"))
    changed_at = _parse_event_time(event.get("changed"))
    ticket_availability = event.get("ticket_availability") or {}
    attributes = {
        "summary": _text(event.get("summary")).strip(),
        "description": _text(event.get("description")).strip(),
        "url": event.get("url"),
        "start_at": start_at.isoformat() if start_at else None,
        "end_at": end_at.isoformat() if end_at else None,
        "changed_at": changed_at.isoformat() if changed_at else None,
        "venue": {
            "id": venue.get("id"),
            "name": venue.get("name"),
            "address": address.get("localized_address_display") or address.get("address_1"),
            "city": address.get("city"),
            "region": address.get("region"),
            "postal_code": address.get("postal_code"),
        },
        "organization": {
            "id": organization.get("id") or event.get("organization_id"),
            "name": _text(organization.get("name")).strip() or None,
        },
        "category_id": str(event.get("category_id") or "") or None,
        "format_id": str(event.get("format_id") or "") or None,
        "ticket_availability": ticket_availability,
        **evidence,
    }
    citation = SourceCitation(
        source_name="Eventbrite",
        dataset_name="Authorized organization events",
        dataset_id=event_id,
        official_url=str(event.get("url") or f"https://www.eventbrite.com/e/{event_id}"),
        vintage=(changed_at or retrieved).isoformat(),
        retrieved_at=retrieved,
        geographic_level="event venue",
        fields_used=[
            "name",
            "description",
            "status",
            "start",
            "end",
            "venue",
            "organization",
            "category_id",
            "format_id",
            "ticket_availability",
        ],
    )
    return ResourceEvidence(
        entity_id=f"eventbrite:{event_id}",
        kind="community_food_event",
        name=name,
        lat=_float_or_none(venue.get("latitude")),
        lon=_float_or_none(venue.get("longitude")),
        status=str(event.get("status") or "unknown"),
        observed_at=changed_at or retrieved,
        source_citation=citation,
        attributes=attributes,
    )


def event_id_from_webhook(payload: dict[str, Any]) -> str:
    """Extract an event ID only from an allowlisted Eventbrite API URL."""
    api_url = str(payload.get("api_url") or "").strip()
    parsed = urlparse(api_url)
    if parsed.scheme != "https" or parsed.hostname not in EVENTBRITE_API_HOSTS:
        raise ValueError("Webhook api_url is not an Eventbrite API URL")
    match = re.fullmatch(r"/v3/events/(\d+)/?", parsed.path)
    if not match:
        raise ValueError("Webhook does not reference an Eventbrite event")
    return match.group(1)


class EventbriteClient:
    def __init__(self, token: str, *, base_url: str = EVENTBRITE_API_BASE_URL,
                 session: requests.Session | None = None, timeout_seconds: float = 15.0):
        if not token.strip():
            raise ValueError("Eventbrite API token is required")
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.headers = {"Authorization": f"Bearer {token.strip()}", "Accept": "application/json"}

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}/{path.lstrip('/')}",
                headers=self.headers,
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise EventbriteAPIError(f"Eventbrite request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise EventbriteAPIError("Eventbrite returned an unexpected response")
        return payload

    def fetch_event(self, event_id: str) -> dict[str, Any]:
        if not str(event_id).isdigit():
            raise ValueError("Eventbrite event id must be numeric")
        return self._get(f"events/{event_id}/", params={"expand": EVENTBRITE_EXPAND})

    def iter_organization_events(self, organization_id: str, *, statuses: str = "live,started",
                                 max_pages: int = 10) -> Iterable[dict[str, Any]]:
        if not str(organization_id).isdigit():
            raise ValueError("Eventbrite organization id must be numeric")
        continuation: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "status": statuses,
                "expand": EVENTBRITE_EXPAND,
                "page_size": 100,
            }
            if continuation:
                params["continuation"] = continuation
            payload = self._get(f"organizations/{organization_id}/events/", params=params)
            events = payload.get("events") or []
            if not isinstance(events, list):
                raise EventbriteAPIError("Eventbrite events response is malformed")
            yield from (event for event in events if isinstance(event, dict))
            pagination = payload.get("pagination") or {}
            continuation = pagination.get("continuation")
            if not pagination.get("has_more_items") or not continuation:
                break


def build_event_batch(events: Iterable[dict[str, Any]], *, retrieved_at: datetime | None = None) -> ResourceEvidenceBatch:
    retrieved = retrieved_at or datetime.now(timezone.utc)
    normalized = [normalize_event(event, retrieved_at=retrieved) for event in events]
    relevant = [record for record in normalized if record.attributes.get("relevant")]
    citation = SourceCitation(
        source_name="Eventbrite",
        dataset_name="Authorized organization events",
        official_url="https://www.eventbrite.com/platform/docs/organizations",
        vintage=retrieved.isoformat(),
        retrieved_at=retrieved,
        geographic_level="event venue",
        fields_used=["venue", "organization", "ticket_availability", "category_id", "format_id"],
    )
    return ResourceEvidenceBatch(
        source_id="eventbrite_community_events",
        records=relevant,
        citation=citation,
        quality=DataQualityReport(
            status=EvidenceStatus.COMPLETE,
            source_row_count=len(normalized),
            matched_rows=len(relevant),
            excluded_rows=len(normalized) - len(relevant),
            warnings=[
                "Eventbrite public Event Search is retired; only events owned by the authorized organization are evaluated."
            ],
        ),
    )
