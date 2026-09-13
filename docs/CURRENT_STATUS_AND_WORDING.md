# Current Status and Canonical Wording — September 13, 2026

This note keeps the backend documentation aligned with the current LastMile Market demo state. It is documentation only; it does not rename the repository, AWS resources, API paths, Python modules, or working endpoints.

## Current demo baseline

LastMile Market remains an AWS-native hackathon deployment:

- Lovable + React + MapLibre frontend
- Amazon Cognito staff authentication
- ECS Express Gateway + FastAPI backend
- Amazon Bedrock + Strands Agents SDK for Crew Lead orchestration
- Deterministic Scout, Router, Dispatch, and Sentry stages
- Amazon S3 for prepared data, evidence, inventory, cold-chain snapshots, and community preference feeds
- Amazon DynamoDB for findings, operations, review, and audit records
- Amazon Location Service as the preferred AWS-native routing provider when `ROUTING_PROVIDER=aws_location`
- OpenRouteService only as an optional fallback provider

Do not describe Databricks as part of the current live demo deployment. Databricks belongs in the post-hackathon data and intelligence roadmap unless and until a validated Databricks-backed path is promoted beside the existing AWS path.

## Product and role names

Use these names in user-facing or judge-facing copy:

| Older wording | Current wording |
| --- | --- |
| Food Access Advisor | LastMile Market |
| Site Advisor | Scout |
| Route Advisor | Router |
| Mission Operations Agent | Dispatch |
| Watchdog | Sentry |
| Staff Workspace | Crew Desk |

Keep these engineering identifiers unchanged:

| Identifier | Keep as-is because |
| --- | --- |
| `food-access-advisor` | Backend repository, deployment, and API integration name |
| `food-equity-navigator` | Frontend repository and Lovable integration name |
| `agent.py` | Scout implementation file |
| `route_advisor.py` | Router implementation file |
| `watchdog_agent.py` | Sentry implementation file |
| `/api/site-advisor/*` and `/api/route-advisor/*` | Existing public API contract |

## Current backend capability wording

Use this wording for the backend overview:

> LastMile Market helps mobile-grocery teams decide where to serve, how to route a vehicle, what to load, and what changed in the community. The backend combines transparent tract scoring, traffic-aware routing, inventory-aware load planning, source monitoring, and protected human review in one AWS-native workflow.

Use this wording for Brief the Crew:

> Brief the Crew is the fast path. Crew Lead interprets the request, invokes Sentry, Scout, Router, and Dispatch in order, and returns a reviewable mission. The same underlying scoring, routing, inventory, and readiness services are exposed through the individual control-panel pages.

Use this wording for the Mission Optimization Engine:

> Dispatch runs a deterministic Mission Optimization Engine. It selects feasible inventory from S3, protects stop reserves, checks cold-chain and spoilage risk, and allocates food using the Knapsack of Equity objective. The LLM does not choose quantities.

Use this wording for routing:

> Amazon Location Service is the AWS-native routing provider when `ROUTING_PROVIDER=aws_location`. OpenRouteService remains a supported fallback provider and local-development option.

## Community Intelligence wording

The expected S3 object is:

```text
s3://food-access-evidence-576951331959-us-east-1/community/preferences.json
```

Use this wording until real preference data is collected and reviewed:

> Community Intelligence can read an operator-reviewed S3 feed at `community/preferences.json`. For the demo, the uploaded file uses `operator_review_required_demo_seed` rows. Real dispatch should only treat preferences as verified after they come from explicit app, SMS, partner pantry, or operator feedback and pass operator review.

Do not say the current demo seed rows are verified production demand.

Good labels:

- `operator_review_required_demo_seed`
- `demo seed community preference feed`
- `operator-reviewed before real dispatch`
- `explicit app, SMS, partner pantry, or operator feedback`

Avoid these labels unless real data has been collected and reviewed:

- `verified community demand`
- `production preference learning`
- `real customer demand`
- `automatically learned preferences`

## Mission Review output wording

The frontend now condenses Dispatch output. Backend documentation should match that language:

- Mission score strip
- Dispatch food list
- Compact readiness checklist
- Mission Explanation / Under the Hood
- Human Accept/Reject decision
- No vehicle dispatched by the prototype

Use:

> Mission Review shows the score, proposed load, readiness checks, and under-the-hood reasoning in a compact operator view. The detailed evidence remains traceable, but the page is optimized for human approval rather than raw backend field inspection.

## Synthetic-data and safety wording

Use:

> Demo inventory, community preference seed rows, and mission records are synthetic or operator-review-required unless explicitly labeled otherwise. LastMile Market produces recommendations and review records; it does not dispatch a vehicle, transfer inventory, reprice food, or automatically treat unreviewed data as verified.

## Legacy artifact wording

Older files such as design canvases, project trackers, early Bedrock setup notes, and Good Neighbor planning pages may still contain historical terms. Add this header when touching them:

> Legacy planning artifact. Current product name is LastMile Market. Current user-facing crew names are Scout, Router, Dispatch, and Sentry. Repository names, module names, and deployed API paths remain unchanged.

## Deploy-and-record checklist wording

Use this as the final gate before recording:

1. Deploy backend main through CodeBuild and ECS.
2. Confirm `COMMUNITY_PREFERENCES_KEY=community/preferences.json` in ECS.
3. Publish frontend main through Lovable.
4. Run a fresh Brief the Crew mission.
5. Accept the suggested load.
6. Open Mission Review.
7. Confirm Mission score, Dispatch food list, compact readiness checks, and collapsed Under the Hood section.
8. Confirm Community Intelligence is labeled honestly as demo seed/operator-review-required unless verified data has replaced it.
9. Record the final demo.