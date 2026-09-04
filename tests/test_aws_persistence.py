import io
import json
from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

from storage.aws_persistence import (AwsEvidenceStore, AwsFlaggedTractStore,
                                     CHANGES_BY_DETECTED_AT_INDEX,
                                     StorageConfigurationError, _query_all, _scan_all)


class FakeS3:
    def __init__(self): self.objects = {}
    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]
    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


class FakeEvidenceTable:
    def __init__(self): self.items = []
    def put_item(self, Item): self.items.append(Item)
    def query(self, **kwargs):
        if kwargs.get("IndexName") == CHANGES_BY_DETECTED_AT_INDEX:
            changes = [item for item in self.items if item.get("item_type") == "change"]
            return {"Items": sorted(changes, key=lambda item: item["detected_at"], reverse=True)}
        successes = [item for item in self.items if item["record_key"].startswith("SNAPSHOT_SUCCESS#")]
        return {"Items": sorted(successes, key=lambda item: item["record_key"], reverse=True)}


class FakeFlagTable:
    def __init__(self): self.items = {}
    def put_item(self, Item): self.items[(Item["tract_key"], Item["recommendation_type"])] = Item
    def scan(self, **kwargs): return {"Items": list(self.items.values())}
    def update_item(self, Key, ExpressionAttributeValues, **kwargs):
        identity = (Key["tract_key"], Key["recommendation_type"])
        if identity not in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        item = self.items[identity]
        item["status"] = ExpressionAttributeValues[":status"]
        item["last_checked_date"] = ExpressionAttributeValues[":checked"]
        if ":note" in ExpressionAttributeValues:
            item["note"] = ExpressionAttributeValues[":note"]
        return {"Attributes": item}


def test_evidence_payload_lives_in_s3_and_metadata_in_dynamodb():
    table, s3 = FakeEvidenceTable(), FakeS3()
    store = AwsEvidenceStore(table=table, s3_client=s3, table_name="evidence", bucket="bucket")
    snapshot_id = store.save_snapshot(source_id="licenses", scope="urban",
        captured_at="2026-08-29T00:00:00+00:00", status="complete", checksum="abc",
        payload='[{"entity_id":"1"}]', error=None, previous_snapshot_id=None)
    metadata = table.items[0]
    assert metadata["record_key"] == snapshot_id
    assert "records_json" not in metadata
    assert metadata["record_count"] == 1
    assert s3.objects[("bucket", metadata["records_s3_key"])] == b'[{"entity_id":"1"}]'
    assert store.load_previous_success("licenses", "urban")["records"] == [{"entity_id": "1"}]


def test_partial_aws_snapshot_does_not_replace_complete_baseline():
    table, s3 = FakeEvidenceTable(), FakeS3()
    store = AwsEvidenceStore(table=table, s3_client=s3, table_name="evidence", bucket="bucket")
    store.save_snapshot(source_id="osm", scope="urban",
        captured_at="2026-08-29T00:00:00+00:00", status="complete", checksum="complete",
        payload='[{"entity_id":"complete"}]', error=None, previous_snapshot_id=None)
    store.save_snapshot(source_id="osm", scope="urban",
        captured_at="2026-08-30T00:00:00+00:00", status="partial", checksum="partial",
        payload='[{"entity_id":"partial"}]', error="incomplete", previous_snapshot_id=None)
    assert store.load_previous_success("osm", "urban")["records"] == [
        {"entity_id": "complete"}
    ]


def test_change_records_preserve_numeric_values_and_are_readable():
    table, s3 = FakeEvidenceTable(), FakeS3()
    store = AwsEvidenceStore(table=table, s3_client=s3, table_name="evidence", bucket="bucket")
    store.save_changes(snapshot_id="snap", previous_snapshot_id=None, source_id="osm", scope="urban",
        detected_at="2026-08-29T00:00:00+00:00", changes=[{"change_type": "added", "entity_id": "x",
        "before": None, "after": {"lat": 41.8}, "affected_tracts": []}])
    stored = table.items[0]
    assert stored["after"]["lat"] == Decimal("41.8")
    assert store.read_changes(limit=10)[0]["after"]["lat"] == 41.8


def test_flagged_tract_round_trip_and_conditional_missing_update():
    store = AwsFlaggedTractStore(table=FakeFlagTable(), table_name="flags")
    written = store.flag("17031010100", "site", "advisor", population=100, centroid_lat=41.8)
    assert written["status"] == "pending"
    assert store.read("pending")[0]["tract_fips"] == "17031010100"
    updated = store.update("17031010100", "site", "possible_change", "new resource")
    assert updated["note"] == "new resource"
    assert "error" in store.update("missing", "site", "still_needed")


def test_missing_aws_configuration_fails_closed(monkeypatch):
    for name in ("WATCHDOG_EVIDENCE_TABLE", "EVIDENCE_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(StorageConfigurationError, match="WATCHDOG_EVIDENCE_TABLE"):
        AwsEvidenceStore()


def test_paginated_scan_reads_all_pages():
    class Table:
        def scan(self, **kwargs):
            if "ExclusiveStartKey" not in kwargs:
                return {"Items": [{"id": 1}], "LastEvaluatedKey": {"id": 1}}
            return {"Items": [{"id": 2}]}
    assert _scan_all(Table()) == [{"id": 1}, {"id": 2}]


def test_paginated_query_reads_all_pages():
    class Table:
        def query(self, **kwargs):
            if "ExclusiveStartKey" not in kwargs:
                return {"Items": [{"id": 1}], "LastEvaluatedKey": {"id": 1}}
            return {"Items": [{"id": 2}]}
    assert _query_all(Table()) == [{"id": 1}, {"id": 2}]


def test_suppressed_changes_are_hidden_from_normal_reads():
    table, s3 = FakeEvidenceTable(), FakeS3()
    store = AwsEvidenceStore(table=table, s3_client=s3, table_name="evidence", bucket="bucket")
    table.items.extend([
        {"item_type": "change", "record_key": "CHANGE#1", "detected_at": "2026-08-29T00:00:00+00:00",
         "source_id": "osm", "suppressed": True},
        {"item_type": "change", "record_key": "CHANGE#2", "detected_at": "2026-08-30T00:00:00+00:00",
         "source_id": "osm"},
        {"item_type": "change", "record_key": "CHANGE#3", "detected_at": "2026-08-31T00:00:00+00:00",
         "source_id": "osm", "suppressed": False},
    ])

    assert [item["record_key"] for item in store.read_changes(limit=10)] == [
        "CHANGE#3", "CHANGE#2"
    ]
