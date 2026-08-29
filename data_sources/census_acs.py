"""Deterministic U.S. Census ACS 5-year tract adapter."""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import requests

from data_sources.contracts import DataQualityReport, EvidenceStatus, EvidenceValue, SourceCitation, TractEvidence, utc_now

ACS_API_ROOT = "https://api.census.gov/data"
MISSING_SENTINELS = {-999999999, -888888888, -666666666, -555555555, -333333333, -222222222}


@dataclass(frozen=True)
class ACSVariable:
    name: str
    estimate: str
    margin_of_error: str | None
    unit: str


DEFAULT_VARIABLES = (
    ACSVariable("total_population", "B01001_001E", "B01001_001M", "people"),
    ACSVariable("median_household_income", "B19013_001E", "B19013_001M", "USD"),
    ACSVariable("households_total", "B11001_001E", "B11001_001M", "households"),
    ACSVariable("households_no_vehicle", "B08201_002E", "B08201_002M", "households"),
)

PRIORITIZATION_VARIABLES = DEFAULT_VARIABLES + (
    ACSVariable("poverty_universe", "B17001_001E", "B17001_001M", "people"),
    ACSVariable("population_below_poverty", "B17001_002E", "B17001_002M", "people"),
)


def enrich_tracts(tracts: list[dict], evidence: list[TractEvidence]) -> list[dict]:
    """Join ACS measures onto Atlas rows by exact 11-digit tract GEOID."""
    by_geoid = {item.tract_geoid: item for item in evidence}
    enriched = []
    for tract in tracts:
        row = dict(tract)
        item = by_geoid.get(str(tract.get("tract_fips", "")))
        if item:
            for name in ("households_total", "households_no_vehicle", "poverty_universe", "population_below_poverty"):
                value = item.values.get(name)
                row[name] = value.value if value else None
            row["acs_provenance"] = [citation.model_dump(mode="json") for citation in item.source_citations]
            row["acs_quality"] = item.quality.model_dump(mode="json")
        enriched.append(row)
    return enriched


def _parse_number(value: str | int | float | None) -> int | float | None:
    if value in (None, "", "null"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number in MISSING_SENTINELS:
        return None
    return int(number) if number.is_integer() else number


class ACSClient:
    def __init__(self, year: int, api_key: str | None = None, *, session: requests.Session | None = None,
                 timeout_seconds: float = 20.0, clock=utc_now):
        if year < 2009:
            raise ValueError("ACS 5-year API releases begin in 2009")
        self.year = year
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.clock = clock

    @property
    def dataset_url(self) -> str:
        return f"{ACS_API_ROOT}/{self.year}/acs/acs5"

    def fetch_tracts(self, *, state_fips: str, county_fips: str,
                     variables: Iterable[ACSVariable] = DEFAULT_VARIABLES) -> list[TractEvidence]:
        if len(state_fips) != 2 or not state_fips.isdigit():
            raise ValueError("state_fips must be two digits")
        if len(county_fips) != 3 or not county_fips.isdigit():
            raise ValueError("county_fips must be three digits")

        requested = tuple(variables)
        fields = ["NAME"]
        for variable in requested:
            fields.append(variable.estimate)
            if variable.margin_of_error:
                fields.append(variable.margin_of_error)
        params = {"get": ",".join(fields), "for": "tract:*", "in": f"state:{state_fips} county:{county_fips}"}
        if self.api_key:
            params["key"] = self.api_key

        response = self.session.get(self.dataset_url, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            raise ValueError("Census ACS response must contain a header row")
        header = payload[0]
        if not isinstance(header, list) or not {"state", "county", "tract"}.issubset(header):
            raise ValueError("Census ACS response is missing tract geography columns")

        retrieved_at: datetime = self.clock()
        evidence = []
        for row in payload[1:]:
            record = dict(zip(header, row, strict=False))
            geoid = str(record["state"]) + str(record["county"]) + str(record["tract"])
            values, missing_fields, used_fields = {}, [], []
            for variable in requested:
                estimate = _parse_number(record.get(variable.estimate))
                margin = _parse_number(record.get(variable.margin_of_error)) if variable.margin_of_error else None
                used_fields.append(variable.estimate)
                if variable.margin_of_error:
                    used_fields.append(variable.margin_of_error)
                missing_reason = None
                if estimate is None:
                    missing_reason = "missing_or_suppressed_by_census"
                    missing_fields.append(variable.name)
                values[variable.name] = EvidenceValue(field=variable.name, value=estimate, unit=variable.unit,
                    margin_of_error=margin, source_field=variable.estimate, missing_reason=missing_reason)

            status = EvidenceStatus.PARTIAL if missing_fields else EvidenceStatus.COMPLETE
            citation = SourceCitation(source_name="U.S. Census Bureau",
                dataset_name="American Community Survey 5-Year Data", dataset_id=f"{self.year}/acs/acs5",
                official_url=self.dataset_url, vintage=str(self.year), retrieved_at=retrieved_at,
                geographic_level="census tract", geography_vintage="2020", fields_used=used_fields)
            evidence.append(TractEvidence(tract_geoid=geoid, geography_vintage="2020", source_citations=[citation],
                quality=DataQualityReport(status=status, source_row_count=1, matched_rows=1, missing_fields=missing_fields),
                values=values, metadata={"name": record.get("NAME")}))
        return evidence
