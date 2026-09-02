# openrouteservice and prepared resource cache

The public API no longer calls Overpass while a user is waiting. Site and
route resource/ranking endpoints read these S3 objects instead:

- `s3://$RESOURCE_CACHE_BUCKET/$RESOURCE_CACHE_PREFIX/urban.json`
- `s3://$RESOURCE_CACHE_BUCKET/$RESOURCE_CACHE_PREFIX/rural.json`

## Rural tract bootstrap

The route study area is the USDA-classified rural portion of Cook, Kane,
Kendall, Grundy, Will, Kankakee, and McHenry Counties. County membership
alone is not accepted as rural.

Use the same current SRAM workbook and 2020 tract geography used for the
Chicago database:

```powershell
python data/prep_atlas.py --input .\data\raw\food_access_atlas.xlsx --product SRAM --regions rural
python data/prep_acs.py --year 2024 --regions rural

aws s3 cp .\data\atlas_rural_county.db `
  s3://food-access-evidence-576951331959-us-east-1/prepared-data/atlas_rural_fringe.db `
  --profile bedrock-dev `
  --region us-east-1
```

The deployed API reads that object through `RURAL_TRACT_DATA_KEY` (default
`prepared-data/atlas_rural_fringe.db`). It fails closed if the object is
missing; it never substitutes Alexander County or illustrative tracts.

## Resource-cache bootstrap

Run this on a machine that can reach Overpass and AWS:

```powershell
$env:AWS_PROFILE = "bedrock-dev"
$env:AWS_REGION = "us-east-1"
$env:RESOURCE_CACHE_BUCKET = "food-access-evidence-576951331959-us-east-1"
python -m scripts.refresh_resource_cache --scope urban
python -m scripts.refresh_resource_cache --scope rural
```

Then schedule the same command in the ingestion/Watchdog runtime. A failed
refresh leaves the previous S3 object intact, so public API reads keep serving
the last successful snapshot.

The ECS task role needs `s3:GetObject` for the `resource-cache/*` and
`prepared-data/*` prefixes. The refresh runtime additionally needs
`s3:PutObject` for `resource-cache/*`.

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
