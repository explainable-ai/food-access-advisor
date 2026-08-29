from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from data_sources.contracts import DataQualityReport, EvidenceStatus, EvidenceValue, SourceCitation, TractEvidence


def citation():
    return SourceCitation(source_name="Census", dataset_name="ACS", official_url="https://api.census.gov/data",
        vintage="2024", retrieved_at=datetime(2026, 8, 29, tzinfo=timezone.utc), geographic_level="tract")


def test_tract_geoid_must_be_eleven_digits():
    with pytest.raises(ValidationError):
        TractEvidence(tract_geoid="17031", geography_vintage="2020", source_citations=[citation()],
            quality=DataQualityReport(status=EvidenceStatus.COMPLETE))


def test_citation_timestamp_must_be_timezone_aware():
    with pytest.raises(ValidationError):
        SourceCitation(source_name="Census", dataset_name="ACS", official_url="https://api.census.gov/data",
            vintage="2024", retrieved_at=datetime(2026, 8, 29), geographic_level="tract")


def test_quality_match_rate_includes_crosswalks():
    report = DataQualityReport(status=EvidenceStatus.PARTIAL, matched_rows=7, crosswalked_rows=2, unmatched_rows=1)
    assert report.match_rate == 0.9


def test_missing_reason_rejected_for_present_value():
    with pytest.raises(ValidationError):
        EvidenceValue(field="population", value=10, missing_reason="not available")
