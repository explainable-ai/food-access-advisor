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
