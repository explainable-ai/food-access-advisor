"""Safely suppress one known incomplete OSM snapshot and its false removals."""

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


def _all_query(table, **kwargs):
    items = []
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        key = response.get("LastEvaluatedKey")
        if not key:
            return items
        kwargs["ExclusiveStartKey"] = key


def _all_scan(table, **kwargs):
    items = []
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        key = response.get("LastEvaluatedKey")
        if not key:
            return items
        kwargs["ExclusiveStartKey"] = key


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--source-scope", default="osm_resources#urban")
    parser.add_argument("--record-count", type=int, default=20)
    parser.add_argument("--expected-change-count", type=int, default=575)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    table = dynamodb.Table(args.table)
    snapshots = _all_query(
        table,
        KeyConditionExpression=(
            Key("source_scope").eq(args.source_scope)
            & Key("record_key").begins_with("SNAPSHOT_SUCCESS#")
        ),
    )
    candidates = [
        item for item in snapshots
        if item.get("item_type") == "snapshot"
        and item.get("status") == "complete"
        and int(item.get("record_count", -1)) == args.record_count
    ]
    if len(candidates) != 1:
        raise SystemExit(
            f"Refusing cleanup: expected exactly one matching complete snapshot, found {len(candidates)}."
        )
    snapshot = candidates[0]
    snapshot_id = snapshot["record_key"]
    previous_snapshot_id = snapshot.get("previous_snapshot_id")
    previous_matches = [
        item for item in snapshots
        if item.get("record_key") == previous_snapshot_id
        and item.get("status") == "complete"
    ]
    if len(previous_matches) != 1:
        raise SystemExit(
            "Refusing cleanup: could not resolve exactly one prior complete snapshot."
        )
    previous_record_count = int(previous_matches[0].get("record_count", -1))
    expected_removals = previous_record_count - int(snapshot["record_count"])
    if expected_removals != args.expected_change_count:
        raise SystemExit(
            f"Refusing cleanup: baseline/current counts imply {expected_removals} removals, "
            f"not the required {args.expected_change_count}."
        )
    changes = _all_scan(
        table,
        FilterExpression=Attr("item_type").eq("change") & Attr("snapshot_id").eq(snapshot_id),
    )
    types = {item.get("change_type") for item in changes}
    if len(changes) != args.expected_change_count or types != {"removed"}:
        raise SystemExit(
            f"Refusing cleanup: expected {args.expected_change_count} removed changes; "
            f"found {len(changes)} with types {sorted(str(value) for value in types)}."
        )

    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "snapshot_id": snapshot_id,
        "source_scope": snapshot["source_scope"],
        "record_count": int(snapshot["record_count"]),
        "previous_record_count": previous_record_count,
        "linked_removed_changes": len(changes),
    }, indent=2))
    if not args.apply:
        print("Dry run only. Re-run with --apply after reviewing these exact counts.")
        return

    applied_at = datetime.now(timezone.utc).isoformat()
    reason = (
        f"Suppressed {len(changes)} false removals from incomplete OSM response "
        f"({int(snapshot['record_count'])} records vs {previous_record_count} baseline)."
    )
    backup_key = (
        "maintenance-backups/osm-false-removals-"
        f"{snapshot_id.replace(':', '-').replace('#', '-')}.json"
    )
    s3 = boto3.client("s3", region_name=args.region)
    try:
        s3.head_object(Bucket=args.bucket, Key=backup_key)
        print(f"Reusing existing pristine backup: s3://{args.bucket}/{backup_key}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NotFound"}:
            raise
        s3.put_object(
            Bucket=args.bucket,
            Key=backup_key,
            Body=json.dumps(
                {"snapshot": snapshot, "changes": changes},
                default=_json_default,
                sort_keys=True,
            ).encode(),
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
        )
    table.update_item(
        Key={"source_scope": snapshot["source_scope"], "record_key": snapshot_id},
        UpdateExpression="SET cleanup_backup_s3_key=:backup",
        ExpressionAttributeValues={":backup": backup_key},
    )

    for item in changes:
        table.update_item(
            Key={"source_scope": item["source_scope"], "record_key": item["record_key"]},
            UpdateExpression=(
                "SET suppressed=:true, suppression_reason=:reason, suppressed_at=:at"
            ),
            ExpressionAttributeValues={
                ":true": True,
                ":reason": reason,
                ":at": applied_at,
            },
        )
    table.update_item(
        Key={"source_scope": snapshot["source_scope"], "record_key": snapshot_id},
        UpdateExpression=(
            "SET #status=:partial, #error=:reason, cleanup_applied_at=:at, cleanup_backup_s3_key=:backup"
        ),
        ExpressionAttributeNames={"#status": "status", "#error": "error"},
        ExpressionAttributeValues={
            ":partial": "partial",
            ":reason": reason,
            ":at": applied_at,
            ":backup": backup_key,
        },
    )
    print(json.dumps({
        "status": "applied",
        "suppressed_changes": len(changes),
        "snapshot_status": "partial",
        "backup_s3_key": backup_key,
    }, indent=2))


if __name__ == "__main__":
    main()
