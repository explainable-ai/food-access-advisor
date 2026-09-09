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

# Source contracts deliberately constrain the very large City datasets to records
# that can inform grocery access. Filtering at Socrata also makes every scheduled
# run deterministic and avoids treating unrelated restaurants as access changes.
GROCERY_INSPECTION_FILTER = "upper(facility_type) like '%GROCERY%'"
FOOD_ACCESS_BUSINESS_FILTER = " OR ".join((
    "upper(business_activity) like '%GROCER%'",
    "upper(business_activity) like '%SUPERMARKET%'",
    "upper(business_activity) like '%PRODUCE%'",
    "upper(business_activity) like '%FOOD STORE%'",
    "upper(license_description) like '%GROCER%'",
    "upper(license_description) like '%PRODUCE MERCHANT%'",
))
FOOD_ACCESS_TERMS = ("grocer", "supermarket", "produce", "food store")
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
        filters = [GROCERY_INSPECTION_FILTER]
        if since:
            filters.append(f"inspection_date >= '{since}T00:00:00.000'")
        rows, retrieved = self._fetch(FOOD_INSPECTIONS, where=" AND ".join(filters), limit=limit)
        fields = ["inspection_id", "dba_name", "aka_name", "license_", "facility_type", "address",
                  "inspection_date", "inspection_type", "results", "latitude", "longitude"]
        citation = self._citation(FOOD_INSPECTIONS, retrieved, fields)
        records, excluded, unusable = [], 0, 0
        for row in rows:
            entity_id = str(row.get("inspection_id") or "").strip()
            name = str(row.get("dba_name") or row.get("aka_name") or "").strip()
            if not entity_id or not name:
                excluded += 1
                unusable += 1
                continue
            lat, lon = _coordinates(row)
            records.append(ResourceEvidence(entity_id=entity_id, kind="food_inspection", name=name,
                lat=lat, lon=lon, status=row.get("results"), observed_at=_parse_date(row.get("inspection_date")),
                source_citation=citation, attributes={k: row.get(k) for k in fields if k in row}))
        return self._batch(
            FOOD_INSPECTIONS, citation, rows, records, excluded, unusable=unusable
        )

    def fetch_active_food_businesses(self, *, limit: int = 50000) -> ResourceEvidenceBatch:
        rows, retrieved = self._fetch(
            ACTIVE_BUSINESS_LICENSES, where=FOOD_ACCESS_BUSINESS_FILTER, limit=limit
        )
        fields = ["license_id", "account_number", "doing_business_as_name", "legal_name",
                  "license_description", "business_activity", "application_type", "date_issued",
                  "license_status", "license_status_change_date", "expiration_date", "address",
                  "latitude", "longitude"]
        citation = self._citation(ACTIVE_BUSINESS_LICENSES, retrieved, fields)
        records, excluded, unusable = [], 0, 0
        for row in rows:
            searchable = " ".join(str(row.get(field) or "").casefold()
                                  for field in ("license_description", "business_activity"))
            if not any(term in searchable for term in FOOD_ACCESS_TERMS):
                excluded += 1
                continue
            entity_id = str(row.get("license_id") or "").strip()
            name = str(row.get("doing_business_as_name") or row.get("legal_name") or "").strip()
            if not entity_id or not name:
                excluded += 1
                unusable += 1
                continue
            lat, lon = _coordinates(row)
            records.append(ResourceEvidence(entity_id=entity_id, kind="licensed_food_business", name=name,
                lat=lat, lon=lon, status=row.get("license_status"),
                observed_at=_parse_date(row.get("expiration_date")), source_citation=citation,
                attributes={k: row.get(k) for k in fields if k in row}))
        return self._batch(
            ACTIVE_BUSINESS_LICENSES, citation, rows, records, excluded, unusable=unusable
        )

    def fetch_farmers_markets(self, *, limit: int = 50000) -> ResourceEvidenceBatch:
        rows, retrieved = self._fetch(FARMERS_MARKETS, limit=limit)
        fields = ["id", "market_name", "address", "location", "start_date", "end_date"]
        citation = self._citation(FARMERS_MARKETS, retrieved, fields)
        records, excluded, unusable = [], 0, 0
        for index, row in enumerate(rows):
            name = str(row.get("market_name") or row.get("name") or "").strip()
            if not name:
                excluded += 1
                unusable += 1
                continue
            entity_id = str(row.get("id") or row.get("market_id") or f"market-{index}")
            lat, lon = _coordinates(row)
            records.append(ResourceEvidence(entity_id=entity_id, kind="farmers_market", name=name,
                lat=lat, lon=lon, status="legacy_directory_record", source_citation=citation,
                attributes={k: row.get(k) for k in fields if k in row}))
        return self._batch(
            FARMERS_MARKETS, citation, rows, records, excluded,
            stale=True, unusable=unusable,
        )

    @staticmethod
    def _batch(dataset, citation, rows, records, excluded, stale=False, unusable=0):
        missing = sum(1 for record in records if record.lat is None or record.lon is None)
        unusable_fraction = unusable / len(rows) if rows else 0.0
        status = (
            EvidenceStatus.PARTIAL
            if unusable and (not records or unusable_fraction > 0.05)
            else EvidenceStatus.STALE_CACHE if stale
            else EvidenceStatus.COMPLETE
        )
        warnings = ["Legacy dataset: verify market dates before operational use."] if stale else []
        if excluded:
            out_of_scope = excluded - unusable
            warnings.append(
                f"{excluded} rows were excluded ({out_of_scope} out-of-scope; "
                f"{unusable} unusable)."
            )
        if unusable and status == EvidenceStatus.COMPLETE:
            warnings.append(
                "A small number of incomplete rows were skipped without invalidating the usable dataset."
            )
        if missing:
            warnings.append(
                f"{missing} in-scope records have no coordinates; non-spatial change detection remains available."
            )
        return ResourceEvidenceBatch(source_id=dataset.source_id, records=records, citation=citation,
            quality=DataQualityReport(status=status, source_row_count=len(rows), matched_rows=len(records),
                excluded_rows=excluded, missing_fields=["coordinates"] if missing else [], warnings=warnings))
