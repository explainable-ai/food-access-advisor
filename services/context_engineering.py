"""Small, versioned context envelopes for the Last Mile Crew.

The envelope is a manifest, not a second source of truth. Structured tools still
own scores, routes, and inventory decisions; this module records the exact
request constraints and identifiers each agent used.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from config import PILOT_RURAL_COUNTY


AgentName = Literal["sentry", "scout", "router", "dispatch"]
FreshnessStatus = Literal["current", "stale", "unknown"]
StudyArea = Literal["chicago_neighborhoods", "rural_fringe"]

CONTEXT_SCHEMA_VERSION = "lastmile-context-v1"

_NUMBER_WORDS = {
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
    "eleven": 11.0,
    "twelve": 12.0,
}
_NUMBER_TOKEN = (
    r"(?:"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"|"
    + "|".join(_NUMBER_WORDS)
    + r")"
)
_RURAL_COUNTY_NAMES = sorted(
    {
        county.lower()
        for area in PILOT_RURAL_COUNTY.get("resource_areas", [])
        for county in area.get("counties", [])
        if county.lower() != "cook"
    }
)
_RURAL_COUNTY_PATTERN = (
    r"\b(?:"
    + "|".join(re.escape(name) for name in _RURAL_COUNTY_NAMES)
    + r")(?:\s+county)?\b"
)


class RequestIntentContext(BaseModel):
    """Constraints extracted from the staff request before any agent runs."""

    request_text: str
    load_lbs: float | None = None
    time_window_hours: float | None = None
    categories: list[str] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=list)
    study_area: StudyArea | None = None
    study_area_source: Literal["explicit", "inferred", "default", "unknown"] = (
        "unknown"
    )


class ProvenanceRef(BaseModel):
    """Traceable source reference without copying a full source payload."""

    source_id: str
    source_type: Literal["structured", "routing", "inventory", "memory"]
    version: str | None = None
    observed_at: str | None = None
    freshness: FreshnessStatus = "unknown"
    authoritative: bool = True


class AgentContextView(BaseModel):
    """Compact manifest of the facts and constraints used by one agent."""

    agent: AgentName
    fact_ids: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)


class ContextEnvelope(BaseModel):
    """Versioned context shared across one Crew run."""

    schema_version: str = CONTEXT_SCHEMA_VERSION
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    request_intent: RequestIntentContext
    agent_views: dict[AgentName, AgentContextView] = Field(default_factory=dict)
    provenance: list[ProvenanceRef] = Field(default_factory=list)
    approved_memory_ids: list[str] = Field(default_factory=list)
    memory_policy: str = "human-approved missions only"


def _number(token: str) -> float:
    lowered = token.lower()
    return _NUMBER_WORDS[lowered] if lowered in _NUMBER_WORDS else float(token.replace(",", ""))


def _first_measure(request: str, unit_pattern: str) -> float | None:
    match = re.search(
        rf"\b({_NUMBER_TOKEN})\s*(?:-|\s)?(?:{unit_pattern})\b",
        request.lower(),
    )
    return _number(match.group(1)) if match else None


def _infer_area(request: str) -> tuple[StudyArea | None, str]:
    lowered = request.lower()
    if re.search(r"\brural\b|\bfringe\b", lowered):
        return "rural_fringe", "inferred"
    if re.search(r"\bchicago\b|\bcook county\b|\bneighborhood", lowered):
        return "chicago_neighborhoods", "inferred"
    if re.search(_RURAL_COUNTY_PATTERN, lowered):
        return "rural_fringe", "inferred"
    return "chicago_neighborhoods", "default"


def build_context_envelope(
    request: str,
    *,
    study_area: StudyArea | None,
    categories: list[str],
    excluded_categories: list[str],
) -> ContextEnvelope:
    """Build deterministic request context before invoking the Crew Lead model."""
    area, area_source = (
        (study_area, "explicit") if study_area else _infer_area(request)
    )
    intent = RequestIntentContext(
        request_text=request.strip(),
        load_lbs=_first_measure(request, r"lb|lbs|pound|pounds"),
        time_window_hours=_first_measure(request, r"hour|hours|hr|hrs"),
        categories=sorted(set(categories)),
        excluded_categories=sorted(set(excluded_categories)),
        study_area=area,
        study_area_source=area_source,
    )
    return ContextEnvelope(request_intent=intent)


def _ids(rows: Any, *keys: str) -> list[str]:
    if not isinstance(rows, list):
        return []
    values: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = next((str(row.get(key, "")).strip() for key in keys if row.get(key)), "")
        if value and value not in values:
            values.append(value)
    return values


def record_agent_context(
    envelope: ContextEnvelope, agent: AgentName, data: dict[str, Any]
) -> None:
    """Record identifiers and constraints, never a duplicate decision payload."""
    intent = envelope.request_intent
    constraints: dict[str, Any] = {"study_area": intent.study_area}
    sources: list[str] = []
    fact_ids: list[str] = []

    if agent == "sentry":
        fact_ids = _ids(data.get("findings") or data.get("changes"), "record_key", "tract_fips")
        sources = _ids(data.get("findings") or data.get("changes"), "source_scope", "source_id")
    elif agent == "scout":
        fact_ids = _ids(data.get("ranked_tracts") or data.get("top_tracts"), "tract_fips")
        sources = _ids(data.get("ranked_tracts") or data.get("top_tracts"), "dataset_version", "source")
        constraints["candidate_count"] = len(fact_ids)
    elif agent == "router":
        fact_ids = _ids(data.get("selected_stops"), "tract_fips", "stop_id")
        constraints.update(
            {
                "load_lbs": intent.load_lbs,
                "time_window_hours": intent.time_window_hours,
                "selected_stop_count": len(fact_ids),
            }
        )
        sources = [
            str(value)
            for value in (data.get("travel_time_source"), data.get("routing_provider"))
            if value
        ]
    else:
        load = data.get("suggested_load") or []
        fact_ids = _ids(load, "item_id", "sku", "item")
        constraints.update(
            {
                "load_lbs": intent.load_lbs,
                "categories": intent.categories,
                "excluded_categories": intent.excluded_categories,
            }
        )
        for check in data.get("readiness_checks") or []:
            for source in check.get("data_used") or []:
                if source not in sources:
                    sources.append(source)

    envelope.agent_views[agent] = AgentContextView(
        agent=agent,
        fact_ids=fact_ids,
        constraints=constraints,
        sources=sources,
    )

    known = {item.source_id for item in envelope.provenance}
    for source in sources:
        if source in known:
            continue
        source_type = (
            "inventory"
            if source.startswith("s3://")
            else ("routing" if "route" in source.lower() else "structured")
        )
        envelope.provenance.append(
            ProvenanceRef(source_id=source, source_type=source_type)
        )
        known.add(source)
