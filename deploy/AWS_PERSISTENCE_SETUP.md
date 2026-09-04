# AWS persistence activation

The application defaults to SQLite so tests and local development remain
credential-free. Deployed API and Watchdog runtimes opt into DynamoDB/S3 with
environment configuration; there is no automatic or silent fallback to SQLite
when AWS mode is selected.

## Runtime configuration

Set these on the ECS task and AgentCore Runtime:

```text
WATCHDOG_STORAGE_PROVIDER=dynamodb
WATCHDOG_EVIDENCE_TABLE=food-access-watchdog-evidence
FLAGGED_TRACTS_TABLE=food-access-flagged-tracts
EVIDENCE_BUCKET=food-access-evidence-576951331959-us-east-1
AWS_REGION=us-east-1
```

Do not set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or
`AWS_SESSION_TOKEN` in either runtime. Attach
`deploy/food-access-runtime-policy.json` to each runtime's IAM role. Local AWS
testing should use an SSO profile.

## Required index

`GET /api/watchdog/changes` reads the `item_type=change` items directly via a
Global Secondary Index (`item_type-detected_at-index`) instead of scanning
the whole table -- the table also holds one `snapshot` item per source per
refresh cycle, typically far more numerous than change events, so a full
Scan was the dominant source of latency on this endpoint (frequently over
the frontend's 20-second read timeout). Create the index once per table:

```bash
aws dynamodb update-table \
  --table-name food-access-watchdog-evidence \
  --attribute-definitions \
      AttributeName=item_type,AttributeType=S \
      AttributeName=detected_at,AttributeType=S \
  --global-secondary-index-updates \
      '[{"Create":{"IndexName":"item_type-detected_at-index","KeySchema":[{"AttributeName":"item_type","KeyType":"HASH"},{"AttributeName":"detected_at","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}}}]'
```

If the table uses `PROVISIONED` billing mode (rather than the default
`PAY_PER_REQUEST`), add a `ProvisionedThroughput` block to the `Create`
object as well. `describe-table` should show the index `ACTIVE` before
relying on it -- backfilling a GSI on an existing table can take a while
depending on table size.

**A GSI is a separate IAM resource from its table.** Creating the index
above is not enough on its own -- `deploy/food-access-runtime-policy.json`
must also grant `dynamodb:Query` on the index's own ARN
(`.../table/food-access-watchdog-evidence/index/item_type-detected_at-index`),
not just the table ARN, or every DynamoDB-backed `/api/watchdog/changes`
request will get `AccessDeniedException`. Re-attach the updated policy
document to each runtime's IAM role after creating the index.

`read_all_changes` falls back to the old full-table Scan (logging a
warning) if the index doesn't exist yet or is still backfilling, so
deploying this code before running the command above degrades to the
previous (slower) behavior rather than erroring -- but the whole point of
this index is to get off that Scan, so create it promptly and confirm the
warning stops appearing in logs.

Note the index's partition key (`item_type`) only has two values, so all
change events share one logical partition -- fine at today's evidence
volume, but if this index itself becomes a bottleneck as history grows, the
next step is a higher-cardinality key (e.g. bucketed by `source_id` or a
coarse time window), not a bigger table.

## Storage boundary

- `food-access-watchdog-evidence` holds snapshot metadata and individual
  added/removed/modified/stale/unavailable events.
- Full normalized source payloads are written to versioned S3 keys below
  `snapshots/{source_id}/{scope}/`. This avoids DynamoDB's item-size ceiling.
- `food-access-flagged-tracts` holds recommendation follow-up state.
- The most recent successful snapshot is loaded from S3 for comparison. A
  failed refresh is recorded separately and can never become a mass removal.

## Local smoke test with SSO

```powershell
aws sso login --profile food-access-admin
$env:AWS_PROFILE="food-access-admin"
$env:AWS_REGION="us-east-1"
$env:WATCHDOG_STORAGE_PROVIDER="dynamodb"
$env:WATCHDOG_EVIDENCE_TABLE="food-access-watchdog-evidence"
$env:FLAGGED_TRACTS_TABLE="food-access-flagged-tracts"
$env:EVIDENCE_BUCKET="food-access-evidence-576951331959-us-east-1"
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Check `http://127.0.0.1:8000/health`, then exercise the API. Remove or set
`WATCHDOG_STORAGE_PROVIDER=sqlite` to return to local SQLite.

## Container check

```powershell
docker build -t food-access-advisor-api .
docker run --rm -p 8000:8000 -e WATCHDOG_STORAGE_PROVIDER=sqlite food-access-advisor-api
```

The container runs as a non-root user, excludes local databases and secrets,
and exposes port 8000 for ECS Express Mode.
