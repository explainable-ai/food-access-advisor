# Food-Access Advisor: Three-Workstream Implementation Package

## Purpose

This package converts the current hackathon prototype into a closed-loop food-access intervention planning product while preserving its three-agent separation of duties.

The product should answer:

1. Where is documented food-access need greatest?
2. Which site or mobile-service intervention should a planner investigate?
3. Is the intervention feasible under the organization's actual constraints?
4. What decision did the human planner make?
5. Did service or access conditions later change?

## Workstreams

| Workstream | Outcome | Specification |
|---|---|---|
| Additional data | Authoritative, versioned tract, resource, and transportation evidence | [01 — Dataset Integration](01-dataset-integration.md) |
| Geospatial prioritization and routing | Explainable site scores and constrained rural route scenarios | [02 — Geospatial Prioritization and Route Optimization](02-geospatial-prioritization-route-optimization.md) |
| Operational and outcome loop | Decision records, evidence snapshots, Watchdog changes, and human verification | [03 — Operational Evidence and Outcome Tracking](03-operational-evidence-outcome-tracking.md) |

Supporting implementation contracts:

- [04 — AWS and AgentCore Architecture](04-aws-agentcore-architecture.md)
- [05 — API Contract](05-api-contract.md)
- [06 — Lovable UI Build Prompt](06-lovable-ui-build-prompt.md)

## Product boundary

The application is decision support. It may identify priority census tracts and compare candidate service scenarios. It must not select an exact property, allocate public funds, claim that an intervention caused an observed outcome, or treat public-data changes as verified real-world changes without human review.

## Preserve the current strengths

- Site Advisor, Route Advisor, and Watchdog remain separate agents with disjoint tools.
- Rankings, routing constraints, verification rules, and status transitions remain deterministic.
- Bedrock explains structured evidence; it does not calculate rankings or invent citations.
- Graph routing remains explicit and non-LLM.
- Existing CLI, FastAPI, and test paths remain operational during incremental migration.

## Recommended implementation order

1. Add typed evidence contracts, source metadata, snapshots, and data-quality reporting.
2. Complete real rural USDA ingestion and add ACS enrichment.
3. Harmonize tract vintages and verify real map boundaries.
4. Improve OSM classification and resource verification.
5. Add versioned site scoring with adjustable weight profiles.
6. Add Amazon Location route matrices/service areas and OR-Tools stop selection.
7. Replace authoritative SQLite state with DynamoDB and S3 snapshots.
8. Add Site and Route AgentCore Runtime entry points.
9. Expand the API for decisions, route scenarios, Watchdog events, and outcomes.
10. Build the Lovable UI against fixtures generated from the final API schemas.
11. Deploy, trace, evaluate, and run end-to-end acceptance tests.

## Hackathon scope

Complete the Site Prioritization Engine and the decision-to-Watchdog loop. Present route optimization as a clearly labeled scenario using visible assumptions until partner operational data is available.
