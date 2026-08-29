"""Typed provenance and quality contracts shared by all data sources."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class EvidenceStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    STALE_CACHE = "stale_cache"
    SAMPLE_MODE = "sample_mode"
    FAILED = "failed"


class SourceCitation(BaseModel):
    source_name: str = Field(min_length=1)
    dataset_name: str = Field(min_length=1)
    dataset_id: str | None = None
    official_url: str = Field(min_length=1)
    vintage: str = Field(min_length=1)
    retrieved_at: datetime
    geographic_level: str = Field(min_length=1)
    geography_vintage: str | None = None
    fields_used: list[str] = Field(default_factory=list)

    @field_validator("retrieved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        return value


class DataQualityReport(BaseModel):
    status: EvidenceStatus
    source_row_count: int = Field(default=0, ge=0)
    matched_rows: int = Field(default=0, ge=0)
    crosswalked_rows: int = Field(default=0, ge=0)
    unmatched_rows: int = Field(default=0, ge=0)
    excluded_rows: int = Field(default=0, ge=0)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def match_rate(self) -> float | None:
        denominator = self.matched_rows + self.crosswalked_rows + self.unmatched_rows
        if denominator == 0:
            return None
        return round((self.matched_rows + self.crosswalked_rows) / denominator, 4)


class EvidenceValue(BaseModel):
    field: str = Field(min_length=1)
    value: int | float | str | bool | None
    unit: str | None = None
    margin_of_error: int | float | None = None
    source_field: str | None = None
    missing_reason: str | None = None
    normalized_value: float | None = Field(default=None, ge=0, le=100)

    @field_validator("missing_reason")
    @classmethod
    def missing_reason_only_for_missing_values(cls, value: str | None, info):
        if value is not None and info.data.get("value") is not None:
            raise ValueError("missing_reason is only valid when value is null")
        return value


class TractEvidence(BaseModel):
    tract_geoid: str
    geography_vintage: str
    source_citations: list[SourceCitation]
    quality: DataQualityReport
    values: dict[str, EvidenceValue] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tract_geoid")
    @classmethod
    def validate_tract_geoid(cls, value: str) -> str:
        if len(value) != 11 or not value.isdigit():
            raise ValueError("tract_geoid must be an 11-digit Census tract GEOID")
        return value


class ResourceEvidence(BaseModel):
    """One normalized public resource record, independent of source schema."""

    entity_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    status: str | None = None
    observed_at: datetime | None = None
    source_citation: SourceCitation
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("observed_at must be timezone-aware")
        return value


class ResourceEvidenceBatch(BaseModel):
    source_id: str = Field(min_length=1)
    records: list[ResourceEvidence] = Field(default_factory=list)
    citation: SourceCitation
    quality: DataQualityReport


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
