# Setup and Run — Backend API and AWS

This is the canonical operator guide for the LastMile Market backend. The repository remains named `food-access-advisor`; do not rename it as part of setup.

## 1. Prerequisites

- Git
- Python 3.12
- AWS CLI v2 for live AWS mode
- Docker for local container validation
- An AWS IAM Identity Center profile with access to the required development account
- Amazon Bedrock model access for the configured inference profile

The production region is `us-east-1`. Use IAM roles in AWS and AWS SSO locally; never place long-lived AWS access keys in `.env`.

## 2. Clone and install

```bash
git clone https://github.com/explainable-ai/food-access-advisor.git
cd food-access-advisor
python -m venv .venv
```

Activate the environment:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies and create a local configuration file:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

The application does not automatically load `.env`. Export the values in your shell or use your normal local environment loader.

## 3. Choose a runtime mode

### Local deterministic development

Set:

```text
AWS_REGION=us-east-1
FOOD_ACCESS_AUTH_MODE=disabled
WATCHDOG_STORAGE_PROVIDER=sqlite
FOOD_ACCESS_CORS_ORIGINS=http://localhost:8080
```

`disabled` is strictly for local development and automated tests. Production must use `required`.

### Live AWS development

Authenticate first:

```bash
aws sso login --profile bedrock-dev
export AWS_PROFILE=bedrock-dev
export AWS_REGION=us-east-1
```

Windows PowerShell:

```powershell
aws sso login --profile bedrock-dev
$env:AWS_PROFILE = "bedrock-dev"
$env:AWS_REGION = "us-east-1"
```

Then configure only the services being exercised.

| Variable | Purpose | Production guidance |
| --- | --- | --- |
| `STRANDS_MODEL_ID` | Bedrock inference profile | Use the approved Claude Haiku 4.5 inference profile |
| `FOOD_ACCESS_CORS_ORIGINS` | Allowed browser origins | Include the final Lovable origin; never use `*` |
| `FOOD_ACCESS_AUTH_MODE` | Staff-action enforcement | `required` |
| `COGNITO_REGION` | Cognito region | `us-east-1` |
| `COGNITO_USER_POOL_ID` | Staff user pool | Inject as configuration, not a secret |
| `COGNITO_APP_CLIENT_ID` | Public browser-client ID | Must match the frontend |
| `COGNITO_STAFF_GROUP` | Authorized group | `staff` |
| `EVIDENCE_BUCKET` | Evidence and prepared-data S3 bucket | Grant scoped task-role access |
| `RESOURCE_CACHE_BUCKET` | Existing-resource snapshots | Often the evidence bucket |
| `INVENTORY_BUCKET` | Inventory objects | Often the evidence bucket |
| `WATCHDOG_STORAGE_PROVIDER` | Sentry persistence | `dynamodb` in AWS |
| `WATCHDOG_EVIDENCE_TABLE` | Evidence findings | DynamoDB table name |
| `FLAGGED_TRACTS_TABLE` | Tract verification state | DynamoDB table name |
| `FOOD_ACCESS_OPERATIONS_TABLE` | Mission and decision audit | DynamoDB table name |
| `ROUTING_PROVIDER` | Road-routing adapter | Production currently uses `openrouteservice` |
| `OPENROUTESERVICE_API_KEY` | Server-side routing credential | Inject from Secrets Manager; never expose to Vite |
| `CENSUS_API_KEY` | Optional Census allowance | Optional for lower-rate public calls |
| `SOCRATA_APP_TOKEN` | Optional Chicago API allowance | Optional for lower-rate public calls |

## 4. Run the API

```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Verify:

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok"}
```

Open Swagger at <http://localhost:8000/docs>.

## 5. Run tests

```bash
pytest
```

The normal test suite mocks external HTTP and AWS boundaries. Tests explicitly intended for live integrations can require an authenticated AWS profile or network access; do not interpret an external-service skip as a unit-test failure.

## 6. Prepare production data

The checked-in demo data supports plumbing tests. Rebuild evidence from approved sources before claiming a new production vintage.

```bash
python data/prep_atlas.py --input data/raw/sram_2025 --product SRAM --distance-method driving --centroids data/raw/census_2020_tract_centroids/2020_Gaz_tracts_national.txt --regions all
python data/prep_acs.py --year 2024 --regions all
python data/prep_tract_boundaries.py
```

Chicago food-insecurity and CTA context:

```bash
PYTHONPATH=. python data/prep_urban_context.py --database data/atlas_pilot_city.db --food-insecurity-snapshot data/raw/gcfd_acs_2024_tracts.json --cta-gtfs data/raw/cta_google_transit.zip --output data/urban_scoring_context.json
```

Preparation scripts validate tract geography, expected counts, and manifests. Do not bypass failed validation or silently replace missing values with zero.

## 7. Initialize S3 inventory

The inventory API expects:

```text
inventory/on-hand.json
inventory/cold-chain.json
```

Upload validated synthetic demo files to the configured `INVENTORY_BUCKET`. The ECS task role needs scoped `s3:GetObject` and `s3:PutObject` access to `inventory/*`; use `s3:ListBucket` only for the inventory prefix if listing is required.

Verify read access:

```bash
curl "$API_BASE_URL/inventory"
curl "$API_BASE_URL/inventory/cold-chain"
```

Do not test POST updates against the production objects until you have a backup or isolated test prefix.

## 8. Test the container locally

```bash
docker build --platform linux/amd64 -t lastmile-market-api:local .
docker run --rm -p 8000:8000 --env FOOD_ACCESS_AUTH_MODE=disabled lastmile-market-api:local
```

In another terminal:

```bash
curl http://localhost:8000/health
```

The Dockerfile pulls Python from ECR Public to reduce Docker Hub rate-limit failures in CodeBuild.

## 9. AWS resources

The deployed path uses:

- CodeBuild for container builds
- Amazon ECR for immutable images
- Amazon ECS Express Gateway service for FastAPI
- Amazon Cognito for staff authentication
- Amazon S3 for prepared tract data, cached resources, evidence, and inventory
- Amazon DynamoDB for Sentry findings, flagged tracts, operations, and human decisions
- AWS Secrets Manager for the OpenRouteService key
- Amazon Bedrock and the Strands Agents SDK for Crew orchestration/explanation
- EventBridge and AgentCore components for scheduled/agent execution where configured

Keep the ECS **task role** separate from the **task execution role**. Application calls use the task role; image pull and logging startup use the execution role.

### Agent runtime and guardrails

The web request path uses one explicitly named Strands `Agent`, **LastMile Market Crew Lead**, which calls four tool-backed roles in strict order: `sentry_check()` → `scout()` → `router()` → `dispatch()`. This is structured tool orchestration, not four agent-to-agent conversations. Scout, Router, and Sentry also expose standalone Strands agent builders; Dispatch is a deterministic mission-preparation stage.

The Crew Lead passes Scout's recorded `top_tracts` to Router and Router's recorded route to Dispatch. `SequentialToolExecutor`, schema validation, fail-fast stage handling, deterministic scoring/routing/load rules, Cognito `staff` authorization, evidence-quality labels, and human Accept/Reject form the runtime guardrails. The deterministic fallback calls the same four stage functions directly and preserves the same response contract.

## 10. Build and deploy

### AWS Console path

1. Open CodeBuild in `us-east-1`.
2. Select `food-access-advisor-api-build`.
3. Choose **Start build** without source overrides.
4. Confirm every phase succeeds and record the pushed ECR tag/digest.
5. Open ECS → Clusters → `default` → `food-access-advisor-api`.
6. Update the service/revision to the new immutable image.
7. Wait for production traffic to reach the new task and for **Deployment complete**.
8. Do not stop the prior task manually during a healthy rolling/canary deployment.

The checked `ecs-express-service.json` is a template and may lag the active console revision. Never redeploy an old image tag or copy placeholder Cognito values into production. Render template placeholders with `deploy/render_ecs_express_service.py` when using the CLI path.

## 11. Post-deployment smoke test

```bash
export API_BASE_URL=https://fo-a5bf5a1a8c9949e0b87db3669a6eb545.ecs.us-east-1.on.aws
curl "$API_BASE_URL/health"
curl "$API_BASE_URL/inventory"
curl "$API_BASE_URL/inventory/cold-chain"
curl "$API_BASE_URL/api/community-signals/sources"
```

Then verify in the browser:

- public tract maps and ranked results load
- a staff user returns through `/auth/callback`
- `/crew/brief` succeeds only while signed in as a member of `staff`
- Mission Review shows named stops and records Accept/Reject decisions
- Access Watch can retry live findings and distinguishes current, partial, stale, and unavailable sources

## 12. Frontend connection

The frontend lives in [`food-equity-navigator`](https://github.com/explainable-ai/food-equity-navigator) and is published at [lastmilemarket.lovable.app](https://lastmilemarket.lovable.app).

The browser needs only public configuration:

```text
VITE_API_BASE_URL=<this API base URL>
VITE_COGNITO_AUTHORITY=<Cognito issuer>
VITE_COGNITO_CLIENT_ID=<same browser client ID>
VITE_COGNITO_REDIRECT_URI=https://lastmilemarket.lovable.app/auth/callback
VITE_COGNITO_LOGOUT_URI=https://lastmilemarket.lovable.app
```

The backend must allow `https://lastmilemarket.lovable.app` in `FOOD_ACCESS_CORS_ORIGINS`. If Lovable's server proxy is used, keep the same API target and verify the proxy forwards the `Authorization` header.

## 13. Troubleshooting

| Symptom | Check |
| --- | --- |
| `401` on staff action | User signed in, frontend sent an access token, token not expired |
| `403` on staff action | Cognito user belongs to `staff` |
| `503` Cognito configuration | Pool/client variables exist on the active ECS revision |
| Inventory object cannot be read | Exact S3 key, configured bucket, task-role permission, valid JSON |
| Crew request returns `503` | ECS logs, Bedrock access, route provider, S3/DynamoDB access, upstream timeout |
| Route network error | OpenRouteService secret injection, task egress, quota, response status |
| Access Watch remains partial | Open the source diagnostic; verify the approved URL contains extractable evidence. JavaScript-only pages may need a rendered-page adapter |
| CodeBuild Docker `429` | Confirm Dockerfile uses ECR Public Python base; rerun only after verifying the source revision |
| SSO token expired | Run `aws sso login --profile bedrock-dev` again |
| New deployment alarm/rollback | Inspect the new task logs and health check; retain or restore the last known-good image digest |

## 14. Security checklist

- Production authentication is `required`.
- CORS is an allowlist; never `*` with authenticated requests.
- No AWS key, password, bearer token, routing key, or Cognito client secret is committed.
- Browser variables contain public configuration only.
- IAM policies are scoped to required actions and object/table resources.
- Demo inventory and missions remain labeled synthetic and `not_for_real_dispatch`.
- Human approval is required before a mission becomes operational.
