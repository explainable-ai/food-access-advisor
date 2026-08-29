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
    assert adapter.session.calls[0][1]["$where"].startswith("inspection_date >=")
    assert adapter.session.calls[0][2] == {"X-App-Token": "token"}


def test_food_license_filter_excludes_non_food_records():
    batch = client([
        {"license_id": "1", "doing_business_as_name": "Grocer", "license_description": "Retail Food Establishment",
         "license_status": "AAI", "latitude": "41.8", "longitude": "-87.6"},
        {"license_id": "2", "doing_business_as_name": "Office", "license_description": "Limited Business License"},
    ]).fetch_active_food_businesses()
    assert [record.name for record in batch.records] == ["Grocer"]
    assert batch.quality.excluded_rows == 1


def test_legacy_market_dataset_is_never_labeled_current():
    batch = client([{"id": "m1", "market_name": "Neighborhood Market", "latitude": "41.8", "longitude": "-87.6"}]).fetch_farmers_markets()
    assert batch.quality.status == EvidenceStatus.STALE_CACHE
    assert batch.records[0].status == "legacy_directory_record"
