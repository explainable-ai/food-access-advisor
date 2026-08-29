"""Official Chicago Socrata adapters with normalized, cited output."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from data_sources.contracts import (
    DataQualityReport,
    EvidenceStatus,
    ResourceEvidence,
    ResourceEvidenceBatch,
    SourceCitation,
    utc_now,
)

SOCRATA_ROOT = "https://data.cityofchicago.org"


@dataclass(frozen=True)
class ChicagoDataset:
    source_id: str
    name: str
    dataset_id: str
    geographic_level: str = "point"
    vintage: str = "live"

    @property
    def api_url(self) -> str:
        return f"{SOCRATA_ROOT}/resource/{self.dataset_id}.json"

    @property
    def official_url(self) -> str:
        return f"{SOCRATA_ROOT}/d/{self.dataset_id}"


FOOD_INSPECTIONS = ChicagoDataset("chicago_food_inspections", "Food Inspections", "4ijn-s7e5")
ACTIVE_BUSINESS_LICENSES = ChicagoDataset(
    "chicago_active_business_licenses", "Business Licenses - Current Active", "uupf-x98q"
)
FARMERS_MARKETS = ChicagoDataset(
    "chicago_farmers_markets", "Farmers Market Dataset", "iqus-3tju", vintage="legacy"
)


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _coordinates(row: dict[str, Any]) -> tuple[float | None, float | None]:
    lat, lon = _float(row.get("latitude")), _float(row.get("longitude"))
    location = row.get("location") or {}
    coords = location.get("coordinates") if isinstance(location, dict) else None
    if (lat is None or lon is None) and isinstance(coords, list) and len(coords) >= 2:
        lon, lat = _float(coords[0]), _float(coords[1])
    return lat, lon


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


class ChicagoSocrataClient:
    def __init__(self, app_token: str | None = None, *, session: requests.Session | None = None,
                 timeout_seconds: float = 30.0, clock=utc_now):
        self.app_token = app_token
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.clock = clock

    def _fetch(self, dataset: ChicagoDataset, *, where: str | None = None,
               limit: int = 50000) -> tuple[list[dict[str, Any]], datetime]:
        if limit < 1 or limit > 50000:
            raise ValueError("limit must be between 1 and 50000")
        headers = {"X-App-Token": self.app_token} if self.app_token else {}
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            # Socrata otherwise returns only one page and does not guarantee
            # implicit row order. The system row ID provides a deterministic
            # order, so page boundaries do not drift between identical runs.
            params: dict[str, Any] = {"$limit": limit, "$offset": offset, "$order": ":id ASC"}
            if where:
                params["$where"] = where
            response = self.session.get(dataset.api_url, params=params, headers=headers,
                                        timeout=self.timeout_seconds)
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list):
                raise ValueError(f"{dataset.source_id} response must be a JSON list")
            rows.extend(page)
            if len(page) < limit:
                break
            offset += len(page)
        return rows, self.clock()

    def _citation(self, dataset: ChicagoDataset, retrieved_at: datetime,
                  fields: list[str]) -> SourceCitation:
        return SourceCitation(source_name="City of Chicago Data Portal", dataset_name=dataset.name,
            dataset_id=dataset.dataset_id, official_url=dataset.official_url, vintage=dataset.vintage,
            retrieved_at=retrieved_at, geographic_level=dataset.geographic_level, fields_used=fields)

    def fetch_food_inspections(self, *, since: str | None = None, limit: int = 50000) -> ResourceEvidenceBatch:
        where = f"inspection_date >= '{since}T00:00:00.000'" if since else None
        rows, retrieved = self._fetch(FOOD_INSPECTIONS, where=where, limit=limit)
        fields = ["inspection_id", "dba_name", "license_", "facility_type", "inspection_date", "results",
                  "latitude", "longitude"]
        citation = self._citation(FOOD_INSPECTIONS, retrieved, fields)
        records, excluded = [], 0
        for row in rows:
            entity_id = str(row.get("inspection_id") or "").strip()
            name = str(row.get("dba_name") or row.get("aka_name") or "").strip()
            if not entity_id or not name:
                excluded += 1
                continue
            lat, lon = _coordinates(row)
            records.append(ResourceEvidence(entity_id=entity_id, kind="food_inspection", name=name,
                lat=lat, lon=lon, status=row.get("results"), observed_at=_parse_date(row.get("inspection_date")),
                source_citation=citation, attributes={k: row.get(k) for k in fields if k in row}))
        return self._batch(FOOD_INSPECTIONS, citation, rows, records, excluded)

    def fetch_active_food_businesses(self, *, limit: int = 50000) -> ResourceEvidenceBatch:
        rows, retrieved = self._fetch(ACTIVE_BUSINESS_LICENSES, limit=limit)
        fields = ["license_id", "account_number", "doing_business_as_name", "license_description",
                  "license_status", "expiration_date", "latitude", "longitude"]
        citation = self._citation(ACTIVE_BUSINESS_LICENSES, retrieved, fields)
        food_terms = ("retail food", "grocery", "shared kitchen", "mobile food", "produce merchant")
        records, excluded = [], 0
        for row in rows:
            description = str(row.get("license_description") or "").lower()
            if not any(term in description for term in food_terms):
                excluded += 1
                continue
            entity_id = str(row.get("license_id") or "").strip()
            name = str(row.get("doing_business_as_name") or row.get("legal_name") or "").strip()
            if not entity_id or not name:
                excluded += 1
                continue
            lat, lon = _coordinates(row)
            records.append(ResourceEvidence(entity_id=entity_id, kind="licensed_food_business", name=name,
                lat=lat, lon=lon, status=row.get("license_status"),
                observed_at=_parse_date(row.get("expiration_date")), source_citation=citation,
                attributes={k: row.get(k) for k in fields if k in row}))
        return self._batch(ACTIVE_BUSINESS_LICENSES, citation, rows, records, excluded)

    def fetch_farmers_markets(self, *, limit: int = 50000) -> ResourceEvidenceBatch:
        rows, retrieved = self._fetch(FARMERS_MARKETS, limit=limit)
        fields = ["id", "market_name", "address", "location", "start_date", "end_date"]
        citation = self._citation(FARMERS_MARKETS, retrieved, fields)
        records, excluded = [], 0
        for index, row in enumerate(rows):
            name = str(row.get("market_name") or row.get("name") or "").strip()
            if not name:
                excluded += 1
                continue
            entity_id = str(row.get("id") or row.get("market_id") or f"market-{index}")
            lat, lon = _coordinates(row)
            records.append(ResourceEvidence(entity_id=entity_id, kind="farmers_market", name=name,
                lat=lat, lon=lon, status="legacy_directory_record", source_citation=citation,
                attributes={k: row.get(k) for k in fields if k in row}))
        return self._batch(FARMERS_MARKETS, citation, rows, records, excluded, stale=True)

    @staticmethod
    def _batch(dataset, citation, rows, records, excluded, stale=False):
        missing = sum(1 for record in records if record.lat is None or record.lon is None)
        status = EvidenceStatus.STALE_CACHE if stale else (EvidenceStatus.PARTIAL if missing or excluded else EvidenceStatus.COMPLETE)
        warnings = ["Legacy dataset: verify market dates before operational use."] if stale else []
        return ResourceEvidenceBatch(source_id=dataset.source_id, records=records, citation=citation,
            quality=DataQualityReport(status=status, source_row_count=len(rows), matched_rows=len(records),
                excluded_rows=excluded, missing_fields=["coordinates"] if missing else [], warnings=warnings))
