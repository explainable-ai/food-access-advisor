# LastMile Market elevate deployment

The same application image supports two deployment surfaces:

- **ECS Express Mode** exposes the complete FastAPI REST API used by the frontend.
- **Amazon Bedrock AgentCore Runtime** invokes the Crew Lead through `POST /invocations`
  and checks `GET /ping`. AgentCore does not expose the other FastAPI paths as ordinary
  public REST endpoints, so ECS remains the frontend API.

## Required environment

Use the existing evidence bucket for inventory, or configure a separate bucket:

```text
INVENTORY_BUCKET=food-access-evidence-576951331959-us-east-1
INVENTORY_PREFIX=
LASTMILE_HUB_LAT=41.8185
LASTMILE_HUB_LON=-87.7266
```

The ECS task role and the new Crew Lead AgentCore execution role need:

```json
{
  "Effect": "Allow",
  "Action": ["s3:GetObject", "s3:PutObject"],
  "Resource": "arn:aws:s3:::food-access-evidence-576951331959-us-east-1/inventory/*"
}
```

They retain their existing read permissions for prepared tract/resource evidence,
DynamoDB evidence, and Bedrock model invocation. Do not grant public S3 access.

## AgentCore container contract

Build the AgentCore image for ARM64 and test its required paths before deployment:

```bash
docker buildx build --platform linux/arm64 -f Dockerfile.agentcore -t lastmile-crew-lead:latest --load .
docker run --rm -p 8080:8080 --env-file .env lastmile-crew-lead:latest
curl http://127.0.0.1:8080/ping
curl -X POST http://127.0.0.1:8080/invocations \
  -H 'content-type: application/json' \
  -d '{"request":"Plan a 4 hour route carrying 1200 lbs in Chicago","study_area":null}'
```

Create a **new Crew Lead runtime** rather than replacing the scheduled Sentry runtime.
Point the AgentCore custom-container project at `Dockerfile.agentcore`, deploy it, and
invoke it with the JSON body above. Keep the current Sentry runtime and EventBridge
schedule unchanged.

## Frontend-facing REST smoke tests

After the ECS image is updated:

```bash
curl "$API_URL/inventory"
curl "$API_URL/inventory/cold-chain"
curl -X POST "$API_URL/crew/brief" -H 'content-type: application/json' \
  -d '{"request":"Plan a 4 hour route carrying 1200 lbs in Chicago","study_area":null}'
curl -X POST "$API_URL/demo/feedback" -H 'content-type: application/json' \
  -d '{"tract_id":"17031840000","households_served":120}'
```

The feedback endpoint is illustrative, returns `persisted: false`, and never modifies
prepared scores or future Scout runs.
