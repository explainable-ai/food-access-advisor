# AWS and Bedrock AgentCore Target Architecture

## Recommendation

Use three separate AgentCore Runtimes with separate IAM roles:

| Runtime | May do | Must not do |
|---|---|---|
| Site Advisor | Read evidence, calculate site rankings, create site recommendations | Verify its own outcomes or change Watchdog history |
| Route Advisor | Read evidence/constraints, run route scenarios, create route recommendations | Verify outcomes or modify site records |
| Watchdog | Read recommendations/snapshots, collect current evidence, create change events, update follow-up status | Create recommendations or change score policy |

All three may share a Bedrock model and Guardrail, but separation must be enforced through tools and IAM, not only prompts.

## Required AWS resources

- Bedrock AgentCore Runtime
- Amazon Bedrock model and Guardrail
- S3 evidence/data bucket
- DynamoDB operational tables
- Amazon Location Service
- EventBridge Scheduler
- Thin Watchdog-invoker Lambda
- CloudWatch and AgentCore Observability
- Secrets Manager and KMS
- IAM roles/policies
- Cognito and API Gateway for deployed UI access

Delay AgentCore Gateway, Memory, Identity, Evaluations, Policy, RDS/PostGIS, Glue, Athena, Step Functions, ECS, and OpenSearch until the MVP demonstrates a concrete need.

## Open-source components

- Strands Agents SDK
- Pandas, GeoPandas, Shapely, PyProj
- PyArrow and GeoParquet
- DuckDB Spatial for local analytical queries
- GTFS Kit or Partridge
- OR-Tools
- FastAPI and Pydantic
- React, TypeScript, and MapLibre
- OpenTelemetry/ADOT

Package deterministic geospatial dependencies in the runtime container or a separately permissioned analysis service. Do not make the LLM perform arithmetic or optimization.

## Data storage

Suggested S3 layout:

```text
raw/{source}/{vintage-or-date}/
processed/tracts/
processed/resources/
processed/accessibility/
processed/route-matrices/
snapshots/analyses/{analysis_id}/
snapshots/watchdog/{watchdog_run_id}/
exports/
```

Use DynamoDB for recommendations, lifecycle state, profiles, runs, change events, verifications, outcomes, and audit transitions.

## Routing hybrid

Amazon Location supplies route times, distances, matrices, isolines, and geometry. OR-Tools consumes the matrix and applies need-aware stop selection, vehicle capacity, maximum duration, service time, required stops, and scenario objectives.

Cache matrices because calculations are billable and repeated candidate sets should not be recomputed.

## Invocation design

The current FastAPI service calls local Python graphs. Target design:

```text
Lovable UI
→ Cognito/API Gateway
→ application API
→ InvokeAgentRuntime for Site or Route
→ deterministic tools and AWS services
```

Add `site_advisor_agentcore_entry.py` and `route_advisor_agentcore_entry.py` alongside the existing Watchdog entry point.

Scheduled flow:

```text
EventBridge Scheduler
→ Lambda async invoker
→ Watchdog AgentCore Runtime
→ DynamoDB pending recommendations
→ external sources/S3 snapshots
→ change events
```

Use an SQS dead-letter queue when deploying the schedule. Add idempotency keys to avoid duplicate Watchdog runs/events.

## Observability

Enable AgentCore/CloudWatch observability and ADOT custom telemetry. Correlate:

- Runtime session ID
- User/planner ID
- Agent name
- Analysis/recommendation/scenario ID
- Watchdog run/change-event ID
- Tool/source call and latency
- Bedrock latency/token use
- Data completeness and score version
- Guardrail interventions
- Errors and retries

## Security

- Separate least-privilege runtime roles.
- Keep API credentials in Secrets Manager.
- Encrypt S3, DynamoDB, logs, and secrets with KMS.
- Restrict CORS to deployed UI origins.
- Do not expose AWS keys or call Bedrock directly from the browser.
- Use presigned access only for approved exports.
- Keep raw public-source ingestion separate from recommendation and outcome permissions.
- Apply Bedrock Guardrails, while retaining deterministic code controls.
