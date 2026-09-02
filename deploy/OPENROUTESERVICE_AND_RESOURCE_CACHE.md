# openrouteservice and prepared resource cache

The public API no longer calls Overpass while a user is waiting. Site and
route resource/ranking endpoints read these S3 objects instead:

- `s3://$RESOURCE_CACHE_BUCKET/$RESOURCE_CACHE_PREFIX/urban.json`
- `s3://$RESOURCE_CACHE_BUCKET/$RESOURCE_CACHE_PREFIX/rural.json`

## One-time bootstrap

Run this on a machine that can reach Overpass and AWS:

```powershell
$env:AWS_PROFILE = "bedrock-dev"
$env:AWS_REGION = "us-east-1"
$env:RESOURCE_CACHE_BUCKET = "food-access-evidence-576951331959-us-east-1"
python scripts/refresh_resource_cache.py
```

Then schedule the same command in the ingestion/Watchdog runtime. A failed
refresh leaves the previous S3 object intact, so public API reads keep serving
the last successful snapshot.

The ECS task role needs `s3:GetObject` for
`arn:aws:s3:::food-access-evidence-576951331959-us-east-1/resource-cache/*`.
The refresh runtime additionally needs `s3:PutObject` for that prefix.

## openrouteservice key

Create a secret and inject it into the ECS container as
`OPENROUTESERVICE_API_KEY`:

```powershell
aws secretsmanager create-secret --name food-access/openrouteservice-api-key --secret-string "<new-key>" --region us-east-1 --profile bedrock-dev
```

Do not put the key in `.env`, a Vite variable, Lovable, the task-definition
`environment` array, or GitHub. Add the secret ARN to the container
definition's `secrets` array and grant the task execution role
`secretsmanager:GetSecretValue`. Set
`RESOURCE_CACHE_BUCKET=food-access-evidence-576951331959-us-east-1` and
`RESOURCE_CACHE_PREFIX=resource-cache` as ordinary ECS environment values.

After registering the task-definition revision, update the existing Fargate
service and force a new deployment. Validate:

```powershell
irm "$ApiUrl/health"
irm "$ApiUrl/api/route-advisor/resources"
```
