# Lovable UI Master Build Prompt

You are designing a polished, responsive planning workspace named **Food-Access Advisor** for food banks, community organizations, local government, and regional planners.

## Non-negotiable architecture

Build a frontend-only Vite + React + TypeScript application.

Do not enable or create:

- Lovable Cloud backend
- Supabase
- Supabase authentication, storage, migrations, or edge functions
- A second application database
- Direct browser calls to Amazon Bedrock or AgentCore
- Browser-visible AWS secrets
- Invented backend endpoints or response fields

The authoritative backend is AWS: FastAPI/API Gateway, Cognito, three Bedrock AgentCore Runtimes, DynamoDB, S3, and Amazon Location.

Use a typed API client and environment variable `VITE_API_BASE_URL`. During UI development, use realistic local fixtures that exactly match the supplied OpenAPI/Pydantic contract. Keep fixtures behind a mock adapter so switching to the live API requires no component rewrites.

## Product promise

Help a planner move through:

```text
Where is need greatest?
→ Which intervention should we investigate?
→ Is it feasible?
→ What did the human decide?
→ Did service or access later change?
```

This is decision support. Never use copy that says the system made a final siting, funding, or route decision.

## Visual direction

Create a professional civic-technology and geospatial-planning product, not a generic AI chat dashboard.

- Accessible light theme with optional dark mode
- Deep navy, warm off-white, muted teal, amber warning, and restrained green
- Strong typography and information hierarchy
- Dense enough for analysts without feeling crowded
- Maps and evidence lead; chat is secondary
- Responsive desktop-first layout, usable on tablets
- WCAG-conscious color contrast, focus states, labels, keyboard navigation, reduced motion, and non-color status indicators

Use MapLibre GL JS. Use reusable tokens and components rather than page-specific styling.

## Navigation

Create:

1. Overview
2. Site Prioritization
3. Route Scenarios
4. Recommendations
5. Watchdog & Outcomes
6. Data & Methodology

Use a collapsible left navigation on desktop and accessible drawer on small screens. Include breadcrumbs and a global region selector.

## Overview

Show:

- Chicago and Alexander County region cards
- Tract map with need/status layers
- Source freshness and quality status
- Pending recommendations
- Watchdog events requiring verification
- Verified outcomes
- Direct actions to start site or route analysis

Map legend distinguishes food-access need, existing verified resources, unverified resources, and follow-up status.

## Site Prioritization

Three-column desktop layout:

**Left controls**

- Region
- Data-product/vintage selector
- Resource categories
- Service radius
- Six score-weight controls
- Weight total validation
- Default reset
- Saved profiles
- Run analysis

**Center map**

- Tract choropleth
- Selected and ranked tracts
- Verified/unverified resources
- Transit stops/routes when available
- Service areas
- Layer controls, legend, zoom-to-selection, and accessible summary

**Right results**

- Ranked tract cards/table
- Total and component score
- Data completeness
- Rank-stability warning
- Population potentially served
- Nearest resource/travel burden
- Evidence sources and vintages
- Open evidence drawer
- Save recommendation

Changing weights recalculates through the deterministic API. Never imply Bedrock computed a ranking.

## Route Scenarios

Collect or edit:

- Depot
- Vehicle count/capacity
- Maximum route duration/stops
- Service time
- Candidate, required, and existing stops
- Drive-time radius
- Objective profile

Display baseline, maximum-coverage, equity-priority, and custom scenarios.

For each scenario show:

- Ordered stop list and route map
- Travel and service time
- Capacity utilization
- Total/new/high-need population served
- Communities gaining or losing coverage
- Remaining gaps
- Assumptions and warnings
- Compare and save controls

Clearly label demonstration assumptions when real operator data is unavailable.

## Recommendations

Provide a filterable review queue with recommendation type, region, date, score/scenario version, decision status, owner, target date, and Watchdog state.

Detail view includes full evidence, weights/constraints, citations, limitations, and lifecycle timeline.

Human actions:

- Accept for investigation
- Reject with required reason
- Defer with date
- Add notes
- Assign owner
- Record planned intervention

Require confirmation before status changes. Never let an agent approve itself.

## Watchdog & Outcomes

Separate:

- Machine-detected data changes
- Verified service changes
- Access outcomes

Event cards show baseline/current evidence, source differences, rule/version, confidence/evidence strength, detection date, and recommendation link.

Human actions:

- Verify resource open
- Verify route/service change
- Still needed
- False signal
- Unrelated change
- Conflicting evidence
- Add verification note

Provide before/after evidence, audit timeline, and outcome fields such as implementation date, capacity, households served, remaining gap, and verification source. Do not collect individual beneficiary records.

## Data & Methodology

Show source cards for USDA LRAM/SRAM, ACS, Census boundaries, OSM, GTFS, Amazon Location, Chicago licenses/inspections, and optional context sources.

For each show configured/enabled, vintage, retrieval date, freshness, geography, methodology, quality state, and warnings.

Explain scoring components, weight assumptions, missing-data policy, tract crosswalks, resource verification, route assumptions, and the distinction between correlation, observation, and causation.

## Shared application states

Design complete, partial, stale-cache, sample-mode, empty, queued, running, success, validation, unauthorized, forbidden, rate-limited, external-source failure, and general error states.

Agent analyses may take 60–120 seconds. Show named progress stages and allow navigation while processing. Do not use an indefinite spinner.

## Authentication and security

Prepare for Amazon Cognito. Implement an auth-provider interface and protected routes, but allow a configurable judge/demo mode if authorized by the backend. Never store AWS credentials. Handle expired sessions, forbidden actions, and logout.

## Engineering quality

- Strict TypeScript
- Typed service layer
- React Router
- TanStack Query for server state
- Accessible component primitives
- Reusable map and evidence components
- Form validation
- Unit tests for calculations displayed by the UI
- Integration tests with fixtures
- No duplicated business rules from the backend
- No fabricated metrics
- No secrets in source
- README with local run, environment, mock/live switch, build, and deployment instructions

## Deliverable sequence

1. Information architecture and tokens
2. Typed API and mock adapters
3. Application shell/navigation
4. Overview
5. Site Prioritization
6. Route Scenarios
7. Recommendations
8. Watchdog & Outcomes
9. Data & Methodology
10. Accessibility, responsive behavior, tests, and documentation

Before implementation, summarize the proposed component tree, routes, state model, API dependencies, assumptions, and any contract gaps. Do not create backend services to fill a contract gap; report it for the AWS backend team.
