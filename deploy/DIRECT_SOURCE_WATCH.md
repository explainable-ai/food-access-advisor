# Community Access Watch: approved direct sources

This integration monitors a small allowlist of first-party public pages. It does
not use Eventbrite, social-media scraping, or an API token.

## Sources

- Greater Chicago Food Depository food finder
- Urban Growers Collective Fresh Moves Mobile Market
- Northern Illinois Food Bank grocery and Mobile Market page
- Beyond Hunger events
- City of Chicago farmers markets
- Chicago Food Policy Action Council events
- Nourishing Hope volunteer opportunities

The registry and exact URLs live in `data_sources/community_sources.py`. Adding a
source is a reviewed code change; callers cannot submit arbitrary URLs.

## Behavior

1. The scheduled Watchdog pass fetches each source independently.
2. Schema.org events are preferred. Relevant official page sections are used
   when structured event markup is unavailable.
3. Results are normalized to the existing `ResourceEvidence` contract and
   compared with the previous successful snapshot.
4. Added, modified, and removed records appear in Community Access Watch for
   human review.
5. A timeout, block page, redirect outside the allowlist, unsupported content
   type, or empty parse becomes a source-health finding. It never becomes a
   false closure.
6. Findings never change scores, routes, missions, or dispatch automatically.

Transit stops are not part of this source registry.

## Endpoints

- `GET /api/community-signals/sources` lists the approved registry.
- `POST /api/community-signals/refresh` performs a staff-authorized refresh.
- `GET /api/watchdog/changes` returns the resulting human-review queue.

## Scheduling

The existing AgentCore Watchdog schedule calls `refresh_additional_sources`.
Direct-source monitoring is included in that pass. No secret or new AWS service
is required. Respect each publisher's terms, robots policy, and reasonable
request limits; the current schedule should remain daily unless a publisher
explicitly permits more frequent checks.

## Verification

After deployment:

```bash
curl --fail --silent --show-error \
  --request POST \
  --header "Authorization: Bearer $STAFF_ACCESS_TOKEN" \
  "https://YOUR_API_HOST/api/community-signals/refresh" | jq
```

Confirm that the response identifies each official source and states that no
ranking, route, mission, or dispatch changed automatically. Then inspect:

```bash
curl --fail --silent --show-error \
  --header "Authorization: Bearer $STAFF_ACCESS_TOKEN" \
  "https://YOUR_API_HOST/api/watchdog/changes?status=open" | jq
```
