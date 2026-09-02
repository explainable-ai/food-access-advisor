import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_sources.lineage import (
    ArtifactZone,
    EvidenceManifest,
    LineageArtifact,
    ManifestStatus,
    PublishedPointer,
    QualityCheck,
    ValidationStatus,
)


NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def artifact(**overrides):
    values = {
        "artifact_id": "raw-acs-2024",
        "source_id": "census_acs5_2024",
        "dataset_id": "2024/acs/acs5",
        "zone": ArtifactZone.RAW,
        "s3_uri": "s3://evidence/raw/source=acs/load_id=20260902/response.json",
        "sha256": "a" * 64,
        "content_type": "application/json",
        "byte_size": 100,
        "row_count": 1331,
        "retrieved_at": NOW,
        "source_vintage": "2024",
        "geography_vintage": "2020",
        "schema_version": "1.0.0",
        "pipeline_version": "1.0.0",
        "validation_status": ValidationStatus.PASSED,
    }
    values.update(overrides)
    return LineageArtifact(**values)


def test_artifact_requires_s3_uri_and_sha256():
    with pytest.raises(ValidationError):
        artifact(s3_uri="https://example.com/file", sha256="not-a-checksum")


def test_passed_artifact_rejects_failed_error_check():
    with pytest.raises(ValidationError):
        artifact(quality_checks=[QualityCheck(check_id="tract_count", passed=False, observed=100, expected=1331)])


def test_approved_manifest_requires_every_source_and_passed_artifact():
    with pytest.raises(ValidationError, match="missing required sources"):
        EvidenceManifest(
            manifest_id="manifest-1",
            snapshot_id="snapshot-1",
            study_area="cook",
            model_version="1.0.0",
            created_at=NOW,
            status=ManifestStatus.APPROVED,
            required_source_ids=["census_acs5_2024", "usda_fara_sram_2025"],
            artifacts=[artifact()],
            output_artifact_ids=["raw-acs-2024"],
            approved_at=NOW,
            approved_by="staff-reviewer",
        )


def test_published_pointer_accepts_only_approved_manifest():
    with pytest.raises(ValidationError, match="approved manifest"):
        PublishedPointer(
            study_area="cook",
            snapshot_id="snapshot-1",
            manifest_uri="s3://evidence/manifests/snapshot-1/manifest.json",
            model_version="1.0.0",
            promoted_at=NOW,
            approval_status=ManifestStatus.CANDIDATE,
        )


def test_source_registry_records_canonical_ht_upload():
    registry_path = Path(__file__).parents[1] / "data_sources" / "source_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    sources = {source["source_id"]: source for source in registry["sources"]}
    ht = sources["cnt_ht_2022_il"]

    assert ht["canonical_upload_sha256"] == "c56734dae3ca4a84025d11409255e59323c623102c94f9741a347e0eeb36979e"
    assert ht["expected_rows"] == 9896
    assert ht["geography_vintage_status"] == "must_be_verified_before_tract_publication"
