# Mission Operations read-only API

This first Mission Operations slice reads the verified synthetic dataset from DynamoDB. It does not create, approve, dispatch, or mutate missions.

## Runtime configuration

Set this environment variable on the ECS Express Gateway service:

```text
FOOD_ACCESS_OPERATIONS_TABLE=food-access-demo-operations
```

The task role needs only `dynamodb:DescribeTable`, `dynamodb:GetItem`, and `dynamodb:Query` on that table. The checked-in runtime policy includes those permissions.

## Endpoints

- `GET /api/operations/summary`
- `GET /api/operations/products`
- `GET /api/operations/inventory-lots`
- `GET /api/operations/vehicles`
- `GET /api/operations/drivers`
- `GET /api/operations/sites`
- `GET /api/operations/permit-rules`
- `GET /api/operations/permits`
- `GET /api/operations/manifest-policies`
- `GET /api/operations/scenarios`
- `GET /api/operations/scenarios/{scenario_id}`

Every returned record must carry all three safety markers:

- `dataset_version=demo-v1`
- `data_classification=synthetic_demo`
- `not_for_real_dispatch=true`

The repository returns a service-unavailable error if a record violates those markers or DynamoDB cannot be read. It uses strongly consistent reads and never uses `Scan`.

## Deployment verification

After building and deploying the image, verify:

```powershell
$api = "https://fo-a5bf5a1a8c9949e0b87db3669a6eb545.ecs.us-east-1.on.aws"
Invoke-RestMethod -Uri "$api/api/operations/summary" -Method Get
```

Expected total: `81`. Expected counts: products 12, lots 30, vehicles 4, drivers 5, sites 6, permit rules 6, permits 6, manifest policies 8, scenarios 4.
