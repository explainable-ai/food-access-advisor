import io
import json

import pytest

from storage.inventory import InventoryObjectNotFound, InventoryStoreError, S3InventoryStore


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            error = RuntimeError("missing")
            error.response = {"Error": {"Code": "NoSuchKey"}}
            raise error
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body


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
