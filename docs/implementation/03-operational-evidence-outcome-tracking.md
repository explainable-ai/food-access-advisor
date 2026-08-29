# Workstream 3: Operational Evidence and Outcome Tracking

## Gap addressed

Public data can identify a promising tract, but it cannot establish whether a location is feasible, whether an organization acted, or whether residents received improved access. This workstream closes the decision-to-outcome loop.

## Organization constraints

Capture:

- Organization and planning region
- Depot and existing stops/routes
- Vehicles, capacity, route duration, and service time
- Service days/time windows
- Staff/volunteer constraints
- Food quantity or service capacity
- Maximum new stops and budget/distance limits
- Candidate host locations and required stops

Do not collect individual beneficiary records for the hackathon.

## Community-grounded evidence

Allow authorized users to record:

- Host willingness
- Parking, safety, ADA access, and transit proximity
- Operating-hour compatibility
- Local trust/familiarity
- Known resources missing from public data
- Temporary or seasonal services
- Community feedback and source
- Feasibility status and reviewer note

Public evidence proposes where to investigate; local evidence determines whether intervention is plausible.

## Recommendation lifecycle

Use explicit transitions:

```text
proposed
→ under_review
→ accepted | rejected | deferred
→ planned
→ implementation_in_progress
→ service_started
→ outcome_verification
→ access_improved | still_needed | closed
```

Store actor, timestamp, reason, note, and previous/new state for every transition. Agents cannot approve their own recommendations.

## Watchdog model

The Watchdog does not observe the real world directly. It compares a saved baseline snapshot with a later snapshot from the same sources and creates a change event.

Process:

1. Save baseline evidence with the recommendation.
2. EventBridge invokes a thin Lambda scheduler.
3. Lambda invokes the Watchdog AgentCore Runtime.
4. Watchdog reads pending recommendations.
5. Deterministic tools retrieve current evidence.
6. Normalize and entity-match old/new resources.
7. A deterministic diff engine creates meaningful change events.
8. Human reviewer confirms, dismisses, or marks unrelated.
9. Store the verified outcome without rewriting original evidence.

## Three levels of change

| Level | Meaning | Example |
|---|---|---|
| Data change | A source record changed | New OSM point |
| Service change | An operating service changed | Verified store or mobile stop opened |
| Access outcome | Reach or burden changed | Travel time fell or households received service |

Never promote a data change directly to an access outcome.

## Machine-detected statuses

- `no_change_detected`
- `possible_change_detected`
- `verification_required`
- `conflicting_evidence`
- `source_unavailable`

## Human-verified outcomes

- `verified_resource_opened`
- `verified_route_added`
- `verified_service_expanded`
- `still_needed`
- `false_signal`
- `unrelated_change`
- `closed`

## Site change rules

Examples:

- New OSM resource alone → possible change, unverified.
- New OSM grocery plus matching active license → verification required.
- Matching recent inspection strengthens evidence but does not replace human verification.
- Removed OSM point alone does not prove closure.
- Confirmed service plus improved road/transit accessibility can support an access-improvement outcome.

Preserve rule ID, previous/current values, sources, dates, match method, and evidence strength.

## Route monitoring limitation

OSM, USDA, and Census generally cannot reveal an organization's mobile-route changes. Route monitoring requires an operational feed or human update:

- CSV upload
- Scheduling-system export/API
- Shared organization table
- Planner-entered stop/schedule updates
- Monthly confirmation workflow

A route update should include route/stop identifiers, coordinates, service date/time, duration, capacity, completion status, households served, and last update. Until a real feed exists, label route updates as demonstration assumptions.

## Durable records

Use DynamoDB for:

- Recommendations and lifecycle state
- Weight/constraint profiles
- Watchdog runs
- Change events
- Verification decisions
- Outcome records
- Audit transitions

Use S3 for immutable evidence snapshots and large GeoJSON/Parquet exports. AgentCore Memory is not the system of record.

## Audit requirements

Every decision/change includes:

- Stable ID
- Recommendation and analysis IDs
- Actor type and identifier
- Timestamp
- Previous and new state
- Reason/note
- Evidence snapshot IDs
- Rule/version
- Idempotency key
- Trace ID

Do not claim that the recommendation caused an observed opening or service change. The defensible statement is that a verified change was observed after the recommendation was recorded.

## MVP

- Save OSM baseline evidence at flag time.
- Schedule Watchdog rechecks.
- Generate additions/removals/category-change events.
- Require confirmation/dismissal in the UI.
- Preserve snapshots and audit history.
- Demonstrate rural comparison with an explicitly labeled sample route-update file.
- Add licenses/inspections and real operator route feeds after the core loop works.
