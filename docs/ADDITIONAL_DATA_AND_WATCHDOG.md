# Additional data and Watchdog change detection

## Sources added

| Source | Adapter | Operational role | Freshness treatment |
|---|---|---|---|
| Chicago Food Inspections (`4ijn-s7e5`) | `data_sources/chicago_socrata.py` | Inspection outcome changes are corroborating evidence about food businesses; they do not by themselves prove grocery access. | Live Socrata retrieval; retrieved timestamp is preserved. |
| Current active Chicago business licenses (`uupf-x98q`) | `data_sources/chicago_socrata.py` | Identifies active licensed food businesses and changes in license status. | Live Socrata retrieval; optional `SOCRATA_APP_TOKEN` increases rate limits. |
| CTA static GTFS | `data_sources/cta_gtfs.py` | Supplies official stops and scheduled-service evidence for transit burden analysis. | Each downloaded ZIP is timestamped. Static GTFS is not real-time service. |
| Chicago Farmers Market Dataset (`iqus-3tju`) | `data_sources/chicago_socrata.py` | Candidate market evidence only. | Always marked `stale`; a person must verify current dates before use. |
| Local pantry/market directories | `data_sources/local_directory.py` | Allows an authoritative partner CSV when no stable public API exists. | Caller must supply owner, official URL, and vintage; invalid rows are excluded and counted. |

The normalized contract is `ResourceEvidence`: stable source/entity ID, kind,
name, coordinates, status, observation time, citation, and source-specific
attributes. `ResourceEvidenceBatch` carries the batch-level quality report.

## How the Watchdog sees changes

Each scheduled AgentCore invocation refreshes the four official sources before
the agent runs. `tools/evidence_snapshots.py` stores an immutable source snapshot
and compares it with the last **successful** snapshot for the same source and
scope. Volatile retrieval timestamps are excluded from checksums.

The diff produces:

- `added`: a stable entity ID appears;
- `removed`: an entity ID is absent from a successful new snapshot;
- `modified`: material normalized fields such as location or status changed;
- `stale`: the source is known to be too old for current operational claims;
- `unavailable`: the fetch failed.

An outage is never converted into an empty successful snapshot. Therefore a
timeout or rate limit produces one `unavailable` event, not a false claim that
every resource was removed. Each source refresh is isolated, so one failure does
not prevent the other sources from being recorded.

Changes are auditable at `GET /api/watchdog/changes`, optionally filtered with
`source_id`. Before production, move the SQLite files to persistent AgentCore
memory or an encrypted DynamoDB/S3-backed store; a container filesystem alone
does not guarantee history survives replacement.
