"""Versioned data-lineage and publication contracts for evidence snapshots."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ArtifactZone(str, Enum):
    LANDING = "landing"
    RAW = "raw"
    QUALITY = "quality"
    QUARANTINE = "quarantine"
    STAGED = "staged"
    CURATED = "curated"
    FEATURES = "features"
    SCORES = "scores"
    PUBLISHED = "published"
    MANIFESTS = "manifests"


class ValidationStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class ManifestStatus(str, Enum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class QualityCheck(BaseModel):
    check_id: str = Field(min_length=1)
    passed: bool
    severity: str = Field(default="error", pattern="^(error|warning)$")
    observed: Any | None = None
    expected: Any | None = None
    message: str | None = None


class LineageArtifact(BaseModel):
    artifact_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    zone: ArtifactZone
    s3_uri: str
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    content_type: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    row_count: int | None = Field(default=None, ge=0)
    retrieved_at: datetime
    source_vintage: str = Field(min_length=1)
    geography_vintage: str | None = None
    schema_version: str = Field(min_length=1)
    pipeline_version: str = Field(min_length=1)
    validation_status: ValidationStatus = ValidationStatus.PENDING
    quality_checks: list[QualityCheck] = Field(default_factory=list)
    parent_artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("s3_uri")
    @classmethod
    def require_s3_uri(cls, value: str) -> str:
        if not value.startswith("s3://") or value.count("/") < 3:
            raise ValueError("s3_uri must identify an object below an S3 bucket")
        return value

    @field_validator("retrieved_at")
    @classmethod
    def retrieved_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone(value, "retrieved_at")

    @model_validator(mode="after")
    def failed_error_check_cannot_be_marked_passed(self):
        failed_errors = [check for check in self.quality_checks if not check.passed and check.severity == "error"]
        if self.validation_status == ValidationStatus.PASSED and failed_errors:
            raise ValueError("an artifact with failed error-level checks cannot be marked passed")
        return self


class EvidenceManifest(BaseModel):
    manifest_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    study_area: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    created_at: datetime
    status: ManifestStatus = ManifestStatus.CANDIDATE
    required_source_ids: list[str] = Field(min_length=1)
    artifacts: list[LineageArtifact] = Field(min_length=1)
    output_artifact_ids: list[str] = Field(min_length=1)
    approved_at: datetime | None = None
    approved_by: str | None = None
    notes: list[str] = Field(default_factory=list)

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone(value, "created_at")

    @field_validator("approved_at")
    @classmethod
    def approved_at_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _require_timezone(value, "approved_at") if value is not None else value

    @model_validator(mode="after")
    def validate_lineage_and_approval(self):
        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifact_id values must be unique within a manifest")

        known_ids = set(artifact_ids)
        unknown_outputs = set(self.output_artifact_ids) - known_ids
        if unknown_outputs:
            raise ValueError(f"output_artifact_ids are not present in artifacts: {sorted(unknown_outputs)}")

        for artifact in self.artifacts:
            unknown_parents = set(artifact.parent_artifact_ids) - known_ids
            if unknown_parents:
                raise ValueError(
                    f"artifact {artifact.artifact_id} has unknown parents: {sorted(unknown_parents)}"
                )

        if len(self.required_source_ids) != len(set(self.required_source_ids)):
            raise ValueError("required_source_ids must be unique")

        if self.status == ManifestStatus.APPROVED:
            present_sources = {artifact.source_id for artifact in self.artifacts}
            missing_sources = set(self.required_source_ids) - present_sources
            if missing_sources:
                raise ValueError(f"approved manifest is missing required sources: {sorted(missing_sources)}")
            if any(artifact.validation_status != ValidationStatus.PASSED for artifact in self.artifacts):
                raise ValueError("every artifact must pass validation before approval")
            if not self.approved_by or self.approved_at is None:
                raise ValueError("approved manifests require approved_by and approved_at")
        return self


class PublishedPointer(BaseModel):
    study_area: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    manifest_uri: str
    model_version: str = Field(min_length=1)
    promoted_at: datetime
    approval_status: ManifestStatus

    @field_validator("manifest_uri")
    @classmethod
    def require_manifest_s3_uri(cls, value: str) -> str:
        if not value.startswith("s3://"):
            raise ValueError("manifest_uri must be an S3 URI")
        return value

    @field_validator("promoted_at")
    @classmethod
    def promoted_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone(value, "promoted_at")

    @model_validator(mode="after")
    def pointer_requires_approved_manifest(self):
        if self.approval_status != ManifestStatus.APPROVED:
            raise ValueError("a published pointer may reference only an approved manifest")
        return self
