# LastMile Market context and memory contract

This contract keeps operational facts, retrieved knowledge, and agent memory
separate. It is additive to the existing S3, DynamoDB, scoring, routing, and
Mission Operations paths.

## Authority order

1. Current structured tool results are authoritative for scores, routes,
   inventory, capacity, and readiness.
2. Reviewed source evidence may explain a structured result but may not replace
   its identifiers or numeric values.
3. Human-approved mission memory may provide historical context but may not
   override current inventory, routing, or readiness checks.
4. Unreviewed Crew drafts are never memory.

## Canonical identifiers

| Entity | Canonical identifier | Required relationship |
| --- | --- | --- |
| Census tract | `tract_fips` | Scout ranking and Router stop use the same value |
| Product | `item_id`, falling back to `sku` during migration | Inventory and cold-chain records resolve to one product |
| Mission | `mission_id` | Path, review payload, and stored review must match |
| Inventory source | S3 URI or future database record version | Dispatch readiness cites the exact source |
| Dataset | `dataset_version` | Derived scores retain their source version |

## Versioned context envelope

`lastmile-context-v1` is returned with every successful or failed Crew run. It
contains:

- the original request and deterministically parsed constraints;
- whether geography was explicit, inferred, or defaulted;
- compact identifiers used by Sentry, Scout, Router, and Dispatch;
- source references and freshness metadata when available;
- IDs of any human-approved memories retrieved in a future context phase.

The envelope is a manifest. It deliberately does not duplicate full tract,
route, inventory, or evidence payloads.

## Memory lifecycle

1. `POST /crew/brief` returns a draft and does not save it.
2. An authenticated staff member reviews the draft in Mission Review.
3. `POST /api/operations/missions/{mission_id}/review` records one immutable
   approval or rejection.
4. Only approved records are returned by `GET /api/operations/mission-memory`.
5. Rejected records remain audit history and are never returned as decision
   memory.

All current records remain `synthetic_demo`, retain
`not_for_real_dispatch: true`, and keep `dispatch_enabled: false`.

## Retrieval boundary

Future RAG may retrieve source methods, community evidence, operating policies,
and cold-chain guidance. It must not calculate tract scores, allocate inventory,
select route geometry, or convert an unreviewed draft into memory.
