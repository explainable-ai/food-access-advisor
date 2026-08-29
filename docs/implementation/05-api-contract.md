# Target API Contract

## Principles

- Preserve current endpoints while adding versioned contracts.
- Return structured evidence before prose.
- Long-running analysis returns `202 Accepted` with an analysis ID and status URL.
- Bedrock explanations are optional fields generated from deterministic results.
- Include source snapshots, score/scenario versions, completeness, and warnings.
- Use idempotency keys for decisions, Watchdog runs, verifications, and outcomes.

## System and sources

```text
GET /api/v1/health
GET /api/v1/config/public
GET /api/v1/data-sources/status
GET /api/v1/geographies
```

Source status includes source ID, configured/enabled, vintage, last retrieval, last successful preparation, freshness, quality state, and warnings.

## Geography and evidence

```text
GET /api/v1/tracts?region={region}
GET /api/v1/tracts/{geoid}/evidence
GET /api/v1/tract-boundaries?region={region}
GET /api/v1/resources?region={region}&category={category}
```

Tract evidence returns food access, demographics, transportation, resource coverage, source citations, snapshot IDs, quality, and missing fields.

## Site analysis

```text
POST /api/v1/analyses/site
GET  /api/v1/analyses/{analysis_id}
GET  /api/v1/analyses/{analysis_id}/rankings
POST /api/v1/analyses/{analysis_id}/brief
```

Request includes region, top N, weight profile, service radius, resource categories, and optional question. Response/rankings include component breakdowns, rank stability, citations, and warnings.

## Route analysis

```text
POST /api/v1/analyses/route
GET  /api/v1/analyses/{analysis_id}
GET  /api/v1/analyses/{analysis_id}/scenarios
GET  /api/v1/scenarios/{scenario_id}
```

Request includes depot, candidate/existing/required stops, vehicles, capacity, maximum duration/stops, service times/windows, drive-time radius, and objective profile. Scenario output includes route geometry, ordered stops, constraints, utilization, coverage gain/loss, assumptions, citations, and warnings.

## Recommendations and decisions

```text
POST /api/v1/recommendations
GET  /api/v1/recommendations
GET  /api/v1/recommendations/{recommendation_id}
POST /api/v1/recommendations/{recommendation_id}/decisions
GET  /api/v1/recommendations/{recommendation_id}/history
```

Decision values: `accepted`, `rejected`, or `deferred`; include reason, note, actor, target date, planned intervention, and expected current version to prevent lost updates.

## Organization constraints and feasibility

```text
GET  /api/v1/organizations/{organization_id}/constraints
PUT  /api/v1/organizations/{organization_id}/constraints
POST /api/v1/candidate-sites/{candidate_id}/reviews
```

Candidate reviews capture host willingness, access, parking, safety, operating compatibility, local evidence, feasibility state, and reviewer note.

## Watchdog and verification

```text
POST /api/v1/watchdog/runs
GET  /api/v1/watchdog/runs/{run_id}
GET  /api/v1/watchdog/events
GET  /api/v1/watchdog/events/{event_id}
POST /api/v1/watchdog/events/{event_id}/verification
GET  /api/v1/recommendations/{recommendation_id}/snapshots
```

Event output includes previous/current snapshot IDs, change level/type, rule/version, evidence strength, match method, source differences, detected status, and human verification.

## Outcomes

```text
POST  /api/v1/outcomes
GET   /api/v1/outcomes
GET   /api/v1/outcomes/{outcome_id}
PATCH /api/v1/outcomes/{outcome_id}
```

Outcomes record implementation status, service dates, capacity, households served, evidence, verification, remaining gap, actor, and audit version. Avoid individual beneficiary data.

## Common envelope

```json
{
  "request_id": "uuid",
  "trace_id": "string",
  "status": "complete",
  "data": {},
  "sources": [],
  "warnings": [],
  "generated_at": "ISO-8601"
}
```

Allowed status values: `queued`, `running`, `complete`, `partial`, `stale_cache`, `sample_mode`, and `failed`.

## Compatibility plan

Keep existing `/api/site-advisor`, `/api/route-advisor`, ranked-tract, resource, evidence, flagged-tract, impact, and boundary endpoints until the Lovable UI has migrated. Implement v1 models alongside current Pydantic schemas and add contract tests before switching clients.
