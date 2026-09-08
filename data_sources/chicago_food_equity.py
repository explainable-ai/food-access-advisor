"""City of Chicago Food Equity Dashboard ArcGIS adapter.

The dashboard is used only as an attributed public data source.  This adapter
does not embed or reproduce the City's map.
"""

from __future__ import annotations

import hashlib
import re
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

SOURCE_ID = "chicago_food_equity_dashboard"
SOURCE_NAME = "City of Chicago Food Equity Dashboard"
DASHBOARD_URL = (
    "https://www.chicago.gov/city/en/sites/advancing-food-equity-in-chicago/"
    "home/data-and-reports/food-equity-dashboard.html"
)
FEATURE_LAYER_URL = (
    "https://services7.arcgis.com/A03QrhyHnDaUmK0W/arcgis/rest/services/"
    "Chicago_Food_Ecosystem_Dataset_/FeatureServer/0"
)
FIELDS = (
    "ObjectId", "Ward", "Community_", "SiteNum", "DBA", "Address",
    "Location", "City_1", "License_Na", "Retail_Foo", "Standard_B",
    "FULL_ADDR", "STATUS", "ACTUAL", "TRANS_ID", "SOURCE", "Latitude",
    "Longitude",
)

# Restaurants, taverns, and coffee shops are intentionally excluded.  Access
# Watch is a focused decision queue, not a copy of every dashboard point.
IN_SCOPE_TERMS = (
    "grocery", "farmers market", "farmer market", "corner store",
    "dollar store", "produce merchant", "urban ag", "community grow",
    "garden", "food grant", "food fund", "food related grant",
    "food-related grant", "catalytic investment", "community development",
    "open space",
)
WHERE_CLAUSE = " OR ".join(
    f"Retail_Foo LIKE '%{term}%'"
    for term in (
        "Grocery", "Farmers Market", "Farmer Market", "Corner Store",
        "Dollar Store", "Produce Merchant", "Urban Ag", "Community Grow",
        "Garden", "Food Grant", "Food Fund", "Food Related Grant",
        "Food-Related Grant", "Catalytic Investment", "Community Development",
        "Open Space",
    )
)


class ChicagoFoodEquityError(RuntimeError):
    """Raised when the public ArcGIS layer cannot provide a complete result."""


def source_registry_entry() -> dict[str, Any]:
    return {
        "source_id": SOURCE_ID,
        "name": SOURCE_NAME,
        "url": DASHBOARD_URL,
        "scope": "chicago",
        "source_type": "food_ecosystem",
        "description": (
            "City food businesses, markets, urban agriculture, grants, and "
            "community investments."
        ),
    }


def _text(value: Any) -> str:
    value = str(value or "").strip()
    return "" if value.casefold() in {"<null>", "null", "none", "nan"} else value


def _float(value: Any) -> float | None:
    try:
        return float(value) if _text(value) else None
    except (TypeError, ValueError):
        return None


def _category(row: dict[str, Any]) -> str:
    return _text(row.get("Retail_Foo") or row.get("SOURCE") or row.get("License_Na"))


def _is_in_scope(row: dict[str, Any]) -> bool:
    searchable = " ".join(
        _text(row.get(field))
        for field in ("Retail_Foo", "SOURCE", "License_Na", "Standard_B", "DBA")
    ).casefold()
    return any(term in searchable for term in IN_SCOPE_TERMS)


def _kind(category: str) -> str:
    value = category.casefold()
    if "farmers market" in value or "farmer market" in value:
        return "farmers_market"
    if "grocery" in value:
        return "grocery_store"
    if "corner store" in value:
        return "corner_store"
    if "dollar store" in value:
        return "dollar_store"
    if "produce merchant" in value:
        return "produce_market"
    if any(term in value for term in ("urban ag", "community grow", "garden", "open space")):
        return "urban_agriculture"
    if any(term in value for term in ("grant", "fund", "investment", "community development")):
        return "food_equity_investment"
    return "food_access_resource"


def _entity_id(row: dict[str, Any], category: str) -> str:
    stable_source_id = _text(row.get("TRANS_ID") or row.get("SiteNum"))
    if stable_source_id:
        identity = f"{category}|{stable_source_id}"
    else:
        identity = "|".join((
            category,
            _text(row.get("DBA")),
            _text(row.get("FULL_ADDR") or row.get("Address") or row.get("Location")),
        ))
    identity = re.sub(r"\s+", " ", identity).strip().casefold()
    if not identity.strip("|"):
        identity = f"object:{_text(row.get('ObjectId'))}"
    return f"city-food-equity:{hashlib.sha256(identity.encode()).hexdigest()[:24]}"


def _layer_vintage(metadata: dict[str, Any]) -> str:
    milliseconds = (metadata.get("editingInfo") or {}).get("dataLastEditDate")
    try:
        updated = datetime.fromtimestamp(float(milliseconds) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return "live public ArcGIS layer"
    return f"ArcGIS layer updated {updated.date().isoformat()}"


class ChicagoFoodEquityClient:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 30.0,
        clock=utc_now,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.clock = clock

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise ChicagoFoodEquityError(f"City food-equity request failed: {exc}") from exc
        except ValueError as exc:
            raise ChicagoFoodEquityError("City food-equity response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ChicagoFoodEquityError("City food-equity response must be a JSON object")
        if payload.get("error"):
            message = payload["error"].get("message") if isinstance(payload["error"], dict) else payload["error"]
            raise ChicagoFoodEquityError(f"ArcGIS error: {message}")
        return payload

    def fetch(self, *, page_size: int = 1000) -> ResourceEvidenceBatch:
        if page_size < 1 or page_size > 1000:
            raise ValueError("page_size must be between 1 and 1000")
        retrieved = self.clock()
        metadata = self._get_json(FEATURE_LAYER_URL, {"f": "json"})
        if "Query" not in str(metadata.get("capabilities") or ""):
            raise ChicagoFoodEquityError("City food-equity layer is not queryable")

        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self._get_json(f"{FEATURE_LAYER_URL}/query", {
                # Filter at the service so thousands of unrelated restaurant,
                # tavern, and coffee-shop rows never cross the network.
                "where": WHERE_CLAUSE,
                "outFields": ",".join(FIELDS),
                "returnGeometry": "false",
                "orderByFields": "ObjectId ASC",
                "resultOffset": offset,
                "resultRecordCount": page_size,
                "f": "json",
            })
            features = payload.get("features")
            if not isinstance(features, list):
                raise ChicagoFoodEquityError("ArcGIS query omitted its feature list")
            page: list[dict[str, Any]] = []
            for feature in features:
                attributes = feature.get("attributes") if isinstance(feature, dict) else None
                if not isinstance(attributes, dict):
                    raise ChicagoFoodEquityError("ArcGIS feature omitted its attributes")
                page.append(attributes)
            rows.extend(page)
            if not page or not payload.get("exceededTransferLimit"):
                break
            offset += len(page)
            if offset > 100_000:
                raise ChicagoFoodEquityError("ArcGIS pagination exceeded the safety limit")

        citation = SourceCitation(
            source_name=SOURCE_NAME,
            dataset_name="Chicago Food Ecosystem Dataset",
            dataset_id=str(metadata.get("serviceItemId") or "c4d99a41c4e44a97a993134a8167f48d"),
            official_url=DASHBOARD_URL,
            vintage=_layer_vintage(metadata),
            retrieved_at=retrieved,
            geographic_level="food business, program, or investment location",
            fields_used=list(FIELDS),
        )
        records: list[ResourceEvidence] = []
        excluded = 0
        missing_coordinates = 0
        for row in rows:
            if not _is_in_scope(row):
                excluded += 1
                continue
            category = _category(row) or "Food access resource"
            name = _text(row.get("DBA")) or category
            lat, lon = _float(row.get("Latitude")), _float(row.get("Longitude"))
            if lat is None or lon is None:
                missing_coordinates += 1
            records.append(ResourceEvidence(
                entity_id=_entity_id(row, category),
                kind=_kind(category),
                name=name,
                lat=lat,
                lon=lon,
                status=_text(row.get("STATUS") or row.get("ACTUAL")) or "listed",
                observed_at=None,
                source_citation=citation,
                attributes={
                    "category": category,
                    "address": _text(row.get("FULL_ADDR") or row.get("Address") or row.get("Location")) or None,
                    "community_area": _text(row.get("Community_")) or None,
                    "ward": _text(row.get("Ward")) or None,
                    "city": _text(row.get("City_1")) or None,
                    "license_name": _text(row.get("License_Na")) or None,
                    "business_activity": _text(row.get("Standard_B")) or None,
                    "source_category": _text(row.get("SOURCE")) or None,
                    "official_source": True,
                },
            ))

        warnings = []
        if missing_coordinates:
            warnings.append(f"{missing_coordinates} in-scope records have no coordinates")
        quality = DataQualityReport(
            status=EvidenceStatus.COMPLETE,
            source_row_count=len(rows),
            matched_rows=len(records),
            excluded_rows=excluded,
            missing_fields=["coordinates"] if missing_coordinates else [],
            warnings=warnings,
        )
        return ResourceEvidenceBatch(
            source_id=SOURCE_ID,
            records=records,
            citation=citation,
            quality=quality,
        )
