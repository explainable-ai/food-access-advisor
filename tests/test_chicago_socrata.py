from datetime import datetime, timezone

from data_sources.chicago_socrata import ChicagoSocrataClient
from data_sources.contracts import EvidenceStatus


class Response:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): return None
    def json(self): return self.payload


class Session:
    def __init__(self, payload): self.payload, self.calls = payload, []
    def get(self, url, params, headers, timeout):
        self.calls.append((url, params, headers, timeout))
        return Response(self.payload)


class PagedSession:
    def __init__(self, pages): self.pages, self.calls = pages, []
    def get(self, url, params, headers, timeout):
        self.calls.append((url, params, headers, timeout))
        return Response(self.pages[len(self.calls) - 1])


def client(payload):
    return ChicagoSocrataClient(app_token="token", session=Session(payload),
        clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc))


def test_food_inspections_are_normalized_and_cited():
    adapter = client([{"inspection_id": "42", "dba_name": "Market", "results": "Pass",
        "inspection_date": "2026-08-01T00:00:00.000", "latitude": "41.8", "longitude": "-87.6"}])
    batch = adapter.fetch_food_inspections(since="2026-01-01")
    assert batch.records[0].entity_id == "42"
    assert batch.records[0].status == "Pass"
    assert batch.citation.dataset_id == "4ijn-s7e5"
    where = adapter.session.calls[0][1]["$where"]
    assert "upper(facility_type) like '%GROCERY%'" in where
    assert "inspection_date >=" in where
    assert adapter.session.calls[0][2] == {"X-App-Token": "token"}


def test_food_license_filter_excludes_non_food_records():
    batch = client([
        {"license_id": "1", "doing_business_as_name": "Grocer", "license_description": "Retail Food Establishment",
         "business_activity": "Retail sale of groceries", "license_status": "AAI",
         "latitude": "41.8", "longitude": "-87.6"},
        {"license_id": "2", "doing_business_as_name": "Office", "license_description": "Limited Business License"},
    ]).fetch_active_food_businesses()
    assert [record.name for record in batch.records] == ["Grocer"]
    assert batch.quality.excluded_rows == 1
    assert batch.quality.status == EvidenceStatus.COMPLETE


def test_missing_coordinates_do_not_make_a_usable_source_partial():
    batch = client([{
        "inspection_id": "42", "dba_name": "Market", "facility_type": "Grocery Store",
        "inspection_date": "2026-08-01T00:00:00.000", "results": "Out of Business",
        "address": "1 Main St",
    }]).fetch_food_inspections()
    assert batch.quality.status == EvidenceStatus.COMPLETE
    assert batch.quality.missing_fields == ["coordinates"]
    assert "non-spatial change detection remains available" in batch.quality.warnings[-1]


def test_active_business_query_is_scoped_at_the_source():
    adapter = client([])
    adapter.fetch_active_food_businesses()
    where = adapter.session.calls[0][1]["$where"]
    assert "business_activity" in where
    assert "GROCERY" in where
    assert "RETAIL FOOD" not in where


def test_legacy_market_dataset_is_never_labeled_current():
    batch = client([{"id": "m1", "market_name": "Neighborhood Market", "latitude": "41.8", "longitude": "-87.6"}]).fetch_farmers_markets()
    assert batch.quality.status == EvidenceStatus.STALE_CACHE
    assert batch.records[0].status == "legacy_directory_record"


def test_socrata_fetches_every_page_in_stable_order():
    pages = [
        [{"inspection_id": "1", "dba_name": "A"}, {"inspection_id": "2", "dba_name": "B"}],
        [{"inspection_id": "3", "dba_name": "C"}],
    ]
    session = PagedSession(pages)
    adapter = ChicagoSocrataClient(session=session,
        clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc))
    batch = adapter.fetch_food_inspections(limit=2)
    assert [record.entity_id for record in batch.records] == ["1", "2", "3"]
    assert [call[1]["$offset"] for call in session.calls] == [0, 2]
    assert all(call[1]["$order"] == ":id ASC" for call in session.calls)
