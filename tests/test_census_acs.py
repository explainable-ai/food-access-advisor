from datetime import datetime, timezone

import pytest

from data_sources.census_acs import ACSClient, _parse_number
from data_sources.contracts import EvidenceStatus


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): return None
    def json(self): return self.payload


class FakeSession:
    def __init__(self, payload): self.payload, self.calls = payload, []
    def get(self, url, params, timeout):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse(self.payload)


HEADER = ["NAME", "B01001_001E", "B01001_001M", "B19013_001E", "B19013_001M",
    "B11001_001E", "B11001_001M", "B08201_002E", "B08201_002M", "state", "county", "tract"]


def test_fetch_tracts_preserves_provenance_and_margin_of_error():
    row = ["Tract 1", "2500", "120", "42000", "2500", "1000", "80", "180", "35", "17", "031", "010100"]
    session = FakeSession([HEADER, row])
    client = ACSClient(2024, api_key="test-key", session=session,
        clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc))
    tract = client.fetch_tracts(state_fips="17", county_fips="031")[0]
    assert tract.tract_geoid == "17031010100"
    assert tract.values["total_population"].value == 2500
    assert tract.values["total_population"].margin_of_error == 120
    assert tract.quality.status == EvidenceStatus.COMPLETE
    assert tract.source_citations[0].dataset_id == "2024/acs/acs5"
    assert session.calls[0]["params"]["key"] == "test-key"


def test_suppressed_value_stays_missing_not_zero():
    row = ["Tract 1", "2500", "120", "-666666666", "-666666666", "1000", "80", "180", "35", "17", "031", "010100"]
    tract = ACSClient(2024, session=FakeSession([HEADER, row])).fetch_tracts(state_fips="17", county_fips="031")[0]
    assert tract.values["median_household_income"].value is None
    assert tract.values["median_household_income"].missing_reason == "missing_or_suppressed_by_census"
    assert tract.quality.status == EvidenceStatus.PARTIAL


@pytest.mark.parametrize("value", ["-999999999", -888888888, "", None, "not-a-number"])
def test_parse_number_returns_none_for_missing_or_invalid(value):
    assert _parse_number(value) is None


def test_invalid_fips_rejected_before_network_call():
    session = FakeSession([HEADER])
    with pytest.raises(ValueError, match="state_fips"):
        ACSClient(2024, session=session).fetch_tracts(state_fips="1", county_fips="031")
    assert session.calls == []
