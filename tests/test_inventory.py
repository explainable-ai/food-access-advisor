import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from storage.inventory import (
    InventoryObjectNotFound,
    InventoryStoreError,
    S3InventoryStore,
    clear_inventory_cache,
)


@pytest.fixture(autouse=True)
def isolated_inventory_cache():
    clear_inventory_cache()
    yield
    clear_inventory_cache()


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.etags = {}
        self.revision = 0
        self.conflict_once = False
        self.get_calls = 0

    def get_object(self, Bucket, Key):
        self.get_calls += 1
        if Key not in self.objects:
            error = RuntimeError("missing")
            error.response = {"Error": {"Code": "NoSuchKey"}}
            raise error
        return {"Body": io.BytesIO(self.objects[Key]), "ETag": self.etags[Key]}

    def put_object(self, Bucket, Key, Body, **kwargs):
        if self.conflict_once and Key == "inventory/on-hand.json" and "IfMatch" in kwargs:
            self.conflict_once = False
            self.put_object(
                Bucket=Bucket,
                Key=Key,
                Body=json.dumps([{"item_id": "bananas", "qty": 4}]).encode(),
            )
        if kwargs.get("IfNoneMatch") == "*" and Key in self.objects:
            error = RuntimeError("conflict")
            error.response = {"Error": {"Code": "PreconditionFailed"}}
            raise error
        if "IfMatch" in kwargs and kwargs["IfMatch"] != self.etags.get(Key):
            error = RuntimeError("conflict")
            error.response = {"Error": {"Code": "PreconditionFailed"}}
            raise error
        self.objects[Key] = Body
        self.revision += 1
        self.etags[Key] = f'"etag-{self.revision}"'


def test_inventory_merge_versions_and_updates_current_object():
    s3 = FakeS3()
    store = S3InventoryStore(bucket="bucket", s3_client=s3)
    merged = store.merge("inventory/on-hand.json", [{"item_id": "apples", "qty": 12}])
    assert merged == [{"item_id": "apples", "qty": 12}]
    assert json.loads(s3.objects["inventory/on-hand.json"]) == merged
    assert any(key.startswith("inventory/versions/") for key in s3.objects)


def test_inventory_missing_is_distinct_from_access_failure():
    store = S3InventoryStore(bucket="bucket", s3_client=FakeS3())
    with pytest.raises(InventoryObjectNotFound):
        store.read("inventory/on-hand.json")


def test_inventory_rejects_rows_without_identity():
    store = S3InventoryStore(bucket="bucket", s3_client=FakeS3())
    with pytest.raises(ValueError):
        store.merge("inventory/on-hand.json", [{"qty": 2}])


@pytest.mark.parametrize("field,value", [
    ("qty", "unknown"),
    ("quantity", -1),
    ("on_hand", float("nan")),
    ("unit_weight_lbs", 0),
    ("weight_lbs", "heavy"),
])
def test_inventory_rejects_invalid_quantity_and_weight_fields(field, value):
    store = S3InventoryStore(bucket="bucket", s3_client=FakeS3())
    with pytest.raises(ValueError, match=field):
        store.merge("inventory/on-hand.json", [{"item_id": "apples", field: value}])


def test_inventory_identity_aliases_merge_the_same_product():
    s3 = FakeS3()
    store = S3InventoryStore(bucket="bucket", s3_client=s3)
    store.write("inventory/on-hand.json", [{"sku": "A", "qty": 2}])

    merged = store.merge("inventory/on-hand.json", [{"item_id": "A", "qty": 5}])

    assert merged == [{"sku": "A", "item_id": "A", "qty": 5}]


def test_inventory_merge_replaces_superseded_value_aliases():
    s3 = FakeS3()
    store = S3InventoryStore(bucket="bucket", s3_client=s3)
    store.write(
        "inventory/on-hand.json",
        [{"item_id": "apples", "on_hand": 2, "unit_weight_lbs": 1.5, "cold_chain_risk": "watch"}],
    )

    merged = store.merge(
        "inventory/on-hand.json",
        [{"item_id": "apples", "qty": 7, "weight_lbs": 2.0, "risk_status": "low"}],
    )

    assert merged == [{"item_id": "apples", "qty": 7, "weight_lbs": 2.0, "risk_status": "low"}]


def test_inventory_merge_retries_after_concurrent_update():
    s3 = FakeS3()
    store = S3InventoryStore(bucket="bucket", s3_client=s3)
    store.write("inventory/on-hand.json", [{"item_id": "apples", "qty": 2}])
    s3.conflict_once = True

    merged = store.merge("inventory/on-hand.json", [{"item_id": "apples", "qty": 7}])

    assert merged == [
        {"item_id": "bananas", "qty": 4},
        {"item_id": "apples", "qty": 7},
    ]


def test_inventory_read_uses_short_lived_cache(monkeypatch):
    monkeypatch.setenv("INVENTORY_CACHE_TTL_SECONDS", "30")
    s3 = FakeS3()
    s3.put_object(
        Bucket="bucket",
        Key="inventory/on-hand.json",
        Body=json.dumps([{"item_id": "apples", "qty": 2}]).encode(),
    )
    store = S3InventoryStore(bucket="bucket", s3_client=s3)

    first = store.read("inventory/on-hand.json")
    first[0]["qty"] = 999
    second = store.read("inventory/on-hand.json")

    assert s3.get_calls == 1
    assert second == [{"item_id": "apples", "qty": 2}]


def test_inventory_write_invalidates_cached_snapshot(monkeypatch):
    monkeypatch.setenv("INVENTORY_CACHE_TTL_SECONDS", "30")
    s3 = FakeS3()
    store = S3InventoryStore(bucket="bucket", s3_client=s3)
    store.write("inventory/on-hand.json", [{"item_id": "apples", "qty": 2}])
    assert store.read("inventory/on-hand.json")[0]["qty"] == 2

    store.write("inventory/on-hand.json", [{"item_id": "apples", "qty": 7}])

    assert store.read("inventory/on-hand.json")[0]["qty"] == 7
    assert s3.get_calls == 2


def test_inventory_read_many_returns_each_requested_object(monkeypatch):
    monkeypatch.setenv("INVENTORY_CACHE_TTL_SECONDS", "30")
    s3 = FakeS3()
    s3.put_object(
        Bucket="bucket",
        Key="inventory/on-hand.json",
        Body=json.dumps([{"item_id": "apples", "qty": 2}]).encode(),
    )
    s3.put_object(
        Bucket="bucket",
        Key="inventory/cold-chain.json",
        Body=json.dumps([{"item_id": "apples", "risk_status": "low"}]).encode(),
    )
    store = S3InventoryStore(bucket="bucket", s3_client=s3)

    snapshot = store.read_many(
        ("inventory/on-hand.json", "inventory/cold-chain.json")
    )

    assert set(snapshot) == {
        "inventory/on-hand.json",
        "inventory/cold-chain.json",
    }
    assert s3.get_calls == 2


def test_concurrent_write_prevents_stale_read_from_repopulating_cache(monkeypatch):
    monkeypatch.setenv("INVENTORY_CACHE_TTL_SECONDS", "30")

    class PausedReadS3(FakeS3):
        def __init__(self):
            super().__init__()
            self.read_started = Event()
            self.allow_read_to_finish = Event()

        def get_object(self, Bucket, Key):
            response = super().get_object(Bucket, Key)
            self.read_started.set()
            assert self.allow_read_to_finish.wait(timeout=2)
            return response

    s3 = PausedReadS3()
    store = S3InventoryStore(bucket="bucket", s3_client=s3)
    store.write("inventory/on-hand.json", [{"item_id": "apples", "qty": 2}])

    with ThreadPoolExecutor(max_workers=1) as executor:
        stale_read = executor.submit(store.read, "inventory/on-hand.json")
        assert s3.read_started.wait(timeout=2)
        store.write("inventory/on-hand.json", [{"item_id": "apples", "qty": 7}])
        s3.allow_read_to_finish.set()
        assert stale_read.result()[0]["qty"] == 2

    assert store.read("inventory/on-hand.json")[0]["qty"] == 7
    assert s3.get_calls == 2
