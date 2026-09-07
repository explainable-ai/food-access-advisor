"""Allowlisted public-source monitoring for Community Access Watch.

The adapters intentionally read only approved first-party pages. They extract
Schema.org events when available and otherwise retain small, relevant text
sections. A parser failure is partial evidence, never proof that programs ended.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests

from data_sources.contracts import (
    DataQualityReport,
    EvidenceStatus,
    ResourceEvidence,
    ResourceEvidenceBatch,
    SourceCitation,
)

MAX_RESPONSE_BYTES = 2_000_000
USER_AGENT = "FoodAccessAdvisor-CommunityWatch/1.0 (+https://github.com/explainable-ai)"

SEMANTIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "direct_assistance": (
        "food distribution", "free groceries", "pop-up pantry", "popup pantry",
        "mobile market", "community fridge", "mobile pantry", "food drive",
        "food pantry", "find food", "meal program", "food assistance",
    ),
    "food_sovereignty_and_justice": (
        "food justice", "urban farm", "community garden", "sustainable agriculture",
        "food desert", "mutual aid",
    ),
    "social_support_and_funding": (
        "community resources", "hunger relief", "volunteer orientation",
        "pantry fundraiser", "social service fair",
    ),
}


@dataclass(frozen=True)
class CommunitySource:
    source_id: str
    name: str
    url: str
    scope: str
    source_type: str
    description: str


APPROVED_SOURCES: tuple[CommunitySource, ...] = (
    CommunitySource(
        "greater_chicago_food_depository", "Greater Chicago Food Depository",
        "https://www.chicagosfoodbank.org/find-food-2/", "chicago",
        "food_assistance", "Pantries, meal programs, distributions, and hours.",
    ),
    CommunitySource(
        "fresh_moves_mobile_market", "Fresh Moves Mobile Market",
        "https://www.urbangrowerscollective.org/fresh-moves-mobile-market", "chicago",
        "mobile_market", "Recurring mobile-market stops and schedules.",
    ),
    CommunitySource(
        "northern_illinois_food_bank", "Northern Illinois Food Bank",
        "https://solvehungertoday.org/get-groceries-resources/", "rural",
        "mobile_market", "Free grocery and mobile-market schedules.",
    ),
    CommunitySource(
        "beyond_hunger_events", "Beyond Hunger",
        "https://www.gobeyondhunger.org/events", "chicago",
        "community_event", "Food-insecurity events, workshops, and fundraisers.",
    ),
    CommunitySource(
        "chicago_farmers_markets", "City of Chicago Farmers Markets",
        "https://www.chicago.gov/city/en/depts/dca/supp_info/farmers_market.html", "chicago",
        "farmers_market", "Official seasonal market dates and locations.",
    ),
    CommunitySource(
        "chicago_food_policy_action_council", "Chicago Food Policy Action Council",
        "https://www.chicagofoodpolicy.com/events-1", "chicago",
        "food_justice", "Food-justice meetings, events, and community submissions.",
    ),
    CommunitySource(
        "nourishing_hope_volunteer", "Nourishing Hope",
        "https://nourishinghopechi.org/volunteer/", "chicago",
        "volunteer", "Food packing, distribution, and delivery opportunities.",
    ),
)

SOURCE_BY_ID = {source.source_id: source for source in APPROVED_SOURCES}
ALLOWED_HOSTS = {urlparse(source.url).hostname for source in APPROVED_SOURCES}
BLOCK_TAGS = {"article", "li", "p", "h1", "h2", "h3", "h4", "h5", "h6"}
SKIP_TAGS = {"script", "style", "svg", "noscript"}
DATE_TIME_RE = re.compile(
    r"\b(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat|sun)(?:day)?s?\b|"
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\b|\b\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)\b|"
    r"\b\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)\b",
    re.IGNORECASE,
)
SOURCE_TYPE_TERMS: dict[str, tuple[str, ...]] = {
    "food_assistance": ("find food", "food pantry", "meal program", "free food"),
    "mobile_market": ("mobile market", "mobile pantry", "free groceries"),
    "community_event": ("hunger", "food", "fundraiser", "benefit", "workshop"),
    "farmers_market": ("farmers market", "farmers' market"),
    "food_justice": ("food justice", "food policy", "food summit"),
    "volunteer": ("volunteer", "pack food", "deliver food", "food distribution"),
}


class CommunitySourceError(RuntimeError):
    """Raised when an approved source cannot provide usable public content."""


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()


def keyword_matches(text: str) -> dict[str, list[str]]:
    haystack = _clean_text(text).casefold()
    return {
        group: [term for term in terms if term.casefold() in haystack]
        for group, terms in SEMANTIC_KEYWORDS.items()
        if any(term.casefold() in haystack for term in terms)
    }


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.json_ld: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._block_tag: str | None = None
        self._block_parts: list[str] = []
        self._skip_depth = 0
        self._json_depth = 0
        self._json_parts: list[str] = []
        self._link_href: str | None = None
        self._link_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        if tag == "script" and values.get("type", "").casefold() == "application/ld+json":
            self._json_depth = 1
            self._json_parts = []
        elif self._json_depth:
            self._json_depth += 1
        if not self._skip_depth and tag in BLOCK_TAGS and self._block_tag is None:
            self._block_tag = tag
            self._block_parts = []
        if not self._skip_depth and tag == "a" and values.get("href"):
            self._link_href = values["href"]
            self._link_parts = []

    def handle_endtag(self, tag: str) -> None:
        if self._json_depth:
            self._json_depth -= 1
            if not self._json_depth:
                value = "".join(self._json_parts).strip()
                if value:
                    self.json_ld.append(value)
        if self._block_tag == tag:
            value = _clean_text(" ".join(self._block_parts))
            if value:
                self.blocks.append(value)
            self._block_tag = None
            self._block_parts = []
        if tag == "a" and self._link_href:
            self.links.append((self._link_href, _clean_text(" ".join(self._link_parts))))
            self._link_href = None
            self._link_parts = []
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._json_depth:
            self._json_parts.append(data)
            return
        if self._skip_depth:
            return
        if self._block_tag:
            self._block_parts.append(data)
        if self._link_href:
            self._link_parts.append(data)


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _is_event(value: dict[str, Any]) -> bool:
    kinds = value.get("@type", [])
    if isinstance(kinds, str):
        kinds = [kinds]
    return any(str(kind).casefold().endswith("event") for kind in kinds)


def _coordinates(value: dict[str, Any]) -> tuple[float | None, float | None]:
    location = value.get("location") or {}
    if isinstance(location, list):
        location = location[0] if location else {}
    geo = location.get("geo") or {} if isinstance(location, dict) else {}
    try:
        lat = float(geo["latitude"]) if geo.get("latitude") not in (None, "") else None
        lon = float(geo["longitude"]) if geo.get("longitude") not in (None, "") else None
    except (TypeError, ValueError):
        return None, None
    return lat, lon


def _location_text(value: dict[str, Any]) -> str | None:
    location = value.get("location") or {}
    if isinstance(location, list):
        location = location[0] if location else {}
    if not isinstance(location, dict):
        return _clean_text(location) or None
    address = location.get("address") or {}
    if isinstance(address, dict):
        address = ", ".join(
            _clean_text(address.get(key))
            for key in ("streetAddress", "addressLocality", "addressRegion", "postalCode")
            if address.get(key)
        )
    return _clean_text(address or location.get("name")) or None


def _stable_id(source_id: str, value: str) -> str:
    identity = DATE_TIME_RE.sub("", _clean_text(value).casefold())
    identity = re.sub(r"\b20\d{2}\b|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", "", identity)
    identity = re.sub(r"\s+", " ", identity).strip()[:240]
    return f"direct:{source_id}:{hashlib.sha256(identity.encode()).hexdigest()[:20]}"


def _citation(source: CommunitySource, retrieved_at: datetime) -> SourceCitation:
    return SourceCitation(
        source_name=source.name,
        dataset_name="Official public schedule and event page",
        dataset_id=source.source_id,
        official_url=source.url,
        vintage="live official webpage",
        retrieved_at=retrieved_at,
        geographic_level="event or service location",
        fields_used=["name", "description", "startDate", "endDate", "location", "url"],
    )


def _record_from_event(source: CommunitySource, event: dict[str, Any], retrieved_at: datetime) -> ResourceEvidence | None:
    name = _clean_text(event.get("name"))
    description = _clean_text(event.get("description"))
    text = f"{name} {description} {source.description}"
    matches = keyword_matches(text)
    if not matches and source.source_type not in {"farmers_market", "mobile_market", "food_justice"}:
        return None
    url = event.get("url") or source.url
    if isinstance(url, dict):
        url = url.get("@id") or source.url
    lat, lon = _coordinates(event)
    identity = _clean_text(event.get("@id") or url or name)
    return ResourceEvidence(
        entity_id=_stable_id(source.source_id, identity),
        kind=source.source_type,
        name=name or source.name,
        lat=lat,
        lon=lon,
        status=_clean_text(event.get("eventStatus")) or "published",
        observed_at=None,
        source_citation=_citation(source, retrieved_at),
        attributes={
            "description": description or None,
            "start_at": event.get("startDate"),
            "end_at": event.get("endDate"),
            "location": _location_text(event),
            "url": str(url),
            "matched_keywords": matches,
            "source_scope": source.scope,
            "official_source": True,
        },
    )


def _relevant_sections(blocks: list[str], source: CommunitySource) -> list[str]:
    selected: list[str] = []
    for index, block in enumerate(blocks):
        window = _clean_text(" ".join(blocks[max(0, index - 1): index + 2]))
        matches = keyword_matches(window)
        source_term = any(
            term in window.casefold()
            for term in SOURCE_TYPE_TERMS.get(source.source_type, ())
        )
        if matches or source_term:
            if 25 <= len(window) <= 1500:
                selected.append(window)
    # Preserve order but eliminate nested/duplicate page fragments.
    result: list[str] = []
    seen: set[str] = set()
    for value in selected:
        key = value.casefold()
        if key not in seen and not any(key in existing.casefold() for existing in result):
            seen.add(key)
            result.append(value)
    return result[:50]


def parse_source_html(source: CommunitySource, html: str, *, retrieved_at: datetime | None = None) -> ResourceEvidenceBatch:
    retrieved = retrieved_at or datetime.now(timezone.utc)
    parser = _PageParser()
    parser.feed(html)
    records: list[ResourceEvidence] = []
    for raw in parser.json_ld:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for value in _walk_json(payload):
            if _is_event(value):
                record = _record_from_event(source, value, retrieved)
                if record:
                    records.append(record)
    if not records:
        for section in _relevant_sections(parser.blocks, source):
            records.append(ResourceEvidence(
                entity_id=_stable_id(source.source_id, section),
                kind=source.source_type,
                name=section[:140].rstrip(),
                status="published",
                observed_at=None,
                source_citation=_citation(source, retrieved),
                attributes={
                    "summary": section,
                    "url": source.url,
                    "matched_keywords": keyword_matches(section),
                    "source_scope": source.scope,
                    "official_source": True,
                },
            ))
    unique = {record.entity_id: record for record in records}
    records = list(unique.values())
    status = EvidenceStatus.COMPLETE if records else EvidenceStatus.PARTIAL
    warnings = [] if records else [
        "The official page was reachable but yielded no parseable in-scope records; previous evidence must remain active."
    ]
    return ResourceEvidenceBatch(
        source_id=source.source_id,
        records=records,
        citation=_citation(source, retrieved),
        quality=DataQualityReport(
            status=status,
            source_row_count=len(parser.blocks),
            matched_rows=len(records),
            excluded_rows=max(0, len(parser.blocks) - len(records)),
            warnings=warnings,
        ),
    )


class CommunitySourceClient:
    def __init__(self, *, session: requests.Session | None = None, timeout_seconds: float = 20.0) -> None:
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def fetch(self, source: CommunitySource) -> ResourceEvidenceBatch:
        expected = urlparse(source.url)
        if expected.scheme != "https" or expected.hostname not in ALLOWED_HOSTS:
            raise ValueError("Community source is not on the approved HTTPS allowlist")
        try:
            response = self.session.get(
                source.url,
                headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": USER_AGENT},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise CommunitySourceError(f"{source.name} request failed: {exc}") from exc
        final_url = urlparse(response.url or source.url)
        if final_url.scheme != "https" or final_url.hostname not in ALLOWED_HOSTS:
            raise CommunitySourceError(f"{source.name} redirected outside the approved source allowlist")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise CommunitySourceError(f"{source.name} response exceeded {MAX_RESPONSE_BYTES} bytes")
        content_type = response.headers.get("Content-Type", "text/html").casefold()
        if "html" not in content_type:
            raise CommunitySourceError(f"{source.name} returned unsupported content type {content_type!r}")
        return parse_source_html(source, response.text)


def source_registry() -> list[dict[str, str]]:
    return [asdict(source) for source in APPROVED_SOURCES]
