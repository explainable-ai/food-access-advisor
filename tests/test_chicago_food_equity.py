from datetime import datetime, timezone

import pytest

from data_sources.chicago_food_equity import (
    ChicagoFoodEquityClient,
    SOURCE_ID,
    source_registry_entry,
)
from services.direct_source_signals import refresh_direct_sources


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.payloads.pop(0))


def metadata():
    return {
        "capabilities": "Query",
        "serviceItemId": "city-food-layer",
        "editingInfo": {"dataLastEditDate": 1782961475405},
    }


def feature(object_id, category, name, address, *, community="RIVERDALE"):
    return {"attributes": {
        "ObjectId": object_id,
        "Retail_Foo": category,
        "DBA": name,
        "Address": address,
        "Community_": community,
        "Ward": "9",
        "City_1": "Chicago",
        "Latitude": 41.65,
        "Longitude": -87.61,
    }}


def test_fetch_paginates_and_keeps_only_access_watch_categories():
    session = Session([
        metadata(),
        {
            "exceededTransferLimit": True,
            "features": [
                feature(1, "DPD Grocery Stores", "South Side Grocer", "1 Main St"),
                feature(2, "BACP Restaurant", "Restaurant", "2 Main St"),
            ],
        },
        {
            "exceededTransferLimit": False,
            "features": [
                feature(3, "Independent Farmers Markets", "Riverdale Market", "3 Main St"),
            ],
        },
    ])
    client = ChicagoFoodEquityClient(
        session=session,
        clock=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc),
    )

    batch = client.fetch(page_size=2)

    assert batch.source_id == SOURCE_ID
    assert batch.quality.status.value == "complete"
    assert batch.quality.source_row_count == 3
    assert batch.quality.matched_rows == 2
    assert batch.quality.excluded_rows == 1
    assert [record.kind for record in batch.records] == ["grocery_store", "farmers_market"]
    assert batch.records[0].attributes["community_area"] == "RIVERDALE"
    assert "Retail_Foo LIKE '%Grocery%'" in session.calls[1][1]["params"]["where"]
    assert session.calls[2][1]["params"]["resultOffset"] == 2


def test_identity_is_stable_when_arcgis_object_id_changes():
    first = ChicagoFoodEquityClient(session=Session([
        metadata(), {"exceededTransferLimit": False, "features": [
            feature(1, "DPD Grocery Stores", "South Side Grocer", "1 Main St")
        ]},
    ])).fetch()
    second = ChicagoFoodEquityClient(session=Session([
        metadata(), {"exceededTransferLimit": False, "features": [
            feature(999, "DPD Grocery Stores", "South Side Grocer", "1 Main St")
        ]},
    ])).fetch()

    assert first.records[0].entity_id == second.records[0].entity_id


def test_registry_identifies_city_dashboard_without_embedding_it():
    source = source_registry_entry()
    assert source["source_id"] == SOURCE_ID
    assert source["url"].startswith("https://www.chicago.gov/")
    assert source["source_type"] == "food_ecosystem"


def test_page_size_is_bounded_by_arcgis_limit():
    with pytest.raises(ValueError, match="between 1 and 1000"):
        ChicagoFoodEquityClient(session=Session([])).fetch(page_size=1001)


class FoodEquityBatchClient:
    def fetch(self):
        return ChicagoFoodEquityClient(session=Session([
            metadata(), {"exceededTransferLimit": False, "features": [
                feature(1, "DPD Grocery Stores", "South Side Grocer", "1 Main St")
            ]},
        ])).fetch()


def test_manual_refresh_includes_city_dashboard_and_completeness_guard():
    calls = []

    def snapshot(source_id, records, **kwargs):
        calls.append((source_id, records, kwargs))
        return {"source_id": source_id, "status": kwargs["status"], "changes": []}

    result = refresh_direct_sources(
        sources=(),
        food_equity_client=FoodEquityBatchClient(),
        snapshot_fn=snapshot,
    )

    assert result["source_count"] == 1
    assert result["healthy_source_count"] == 1
    assert calls[0][0] == SOURCE_ID
    assert calls[0][2]["min_retained_fraction"] == 0.85
    assert calls[0][2]["min_baseline_records"] == 100
