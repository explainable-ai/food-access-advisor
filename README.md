# Food-Access Advisor

A Strands Agents SDK agent for the **Agents for Humans** hackathon (Good Neighbor track).

Community leaders deciding where to put a new farm, market, or food-rescue
route usually work from instinct, not evidence — even though the evidence
already exists in public datasets nobody queries. This project maps
low-income, low-access census tracts (USDA's Food Access Research Atlas)
against what food resources already exist nearby, and answers a plain
question like *"where in Chicago would a new food resource have the
highest impact?"* with a ranked, cited recommendation.

Three agents, split by job and lifecycle, not one agent with a mode flag —
see [Guardrails](#guardrails) for why that split matters:

- **Site Advisor** (`agent.py`) — on-demand, recommends a new fixed site
  for the urban pilot city (Chicago, IL).
- **Route Advisor** (`route_advisor.py`) — on-demand, recommends a route
  or distribution-schedule change instead of a new site for the rural
  pilot county (Alexander County, IL), where a fixed-site store is often
  not viable at rural density. Scaffolded against illustrative sample
  data — see [Data setup](#data-setup).
- **Watchdog** (`watchdog_agent.py`) — scheduled, closes the loop by
  working through both Advisors' flagged tracts and reporting whether a
  resource or route change ever actually appeared/happened.

`orchestration.py` formalizes these three with Strands' `GraphBuilder`
(without adding a fourth agent — see
[Orchestration, API, and the planning-workspace UI](#orchestration-api-and-the-planning-workspace-ui)),
and a React + FastAPI planning workspace (map, ranked-answer workspaces,
follow-up tracking) sits on top for a browser-based demo instead of three
command-line scripts.

## Architecture

```mermaid
flowchart LR
    U["Community organizer\n(plain-language question)"] --> ADV[Site Advisor]
    ADV -->|1| AD[get_low_access_tracts]
    ADV -->|2| ER[get_existing_resources]
    ADV -->|3| GS[score_gaps]
    ADV -->|4| EB[write_evidence_brief]
    ADV -->|5| FLAG[flag_top_tract_for_recheck]

    AD -.reads.-> ATLAS[(USDA Food Access Research Atlas\nLRAM/SRAM, local SQLite)]
    ER -.queries.-> OSM[(OpenStreetMap\nOverpass API)]
    EB -.LLM call.-> BEDROCK[(Amazon Bedrock)]
    FLAG -->|writes pending row, type=site| LOG[(flagged_tracts.db)]

    GS -->|ranked tracts| ADV
    EB -->|cited paragraph| ADV
    ADV --> ANSWER["Ranked recommendation\n+ citable brief"]

    P["Regional planner\n(routing question)"] --> RA[Route Advisor]
    RA -->|1| ADR[get_low_access_rural_tracts]
    RA -->|2| ERR[get_rural_existing_resources]
    RA -->|3| GS
    RA -->|4| RB[write_route_brief]
    RA -->|5| FLAGR[flag_top_route_for_recheck]

    ADR -.reads.-> RATLAS[(USDA Atlas, rural county\nlocal SQLite)]
    ERR -.queries.-> OSM
    RB -.LLM call, discloses\ncapacity trade-off.-> BEDROCK
    FLAGR -->|writes pending row, type=route| LOG

    RB -->|cited paragraph| RA
    RA --> RANSWER["Ranked route recommendation\n+ citable brief"]

    SCHED["Scheduled trigger\n(e.g. monthly)"] --> WD[Watchdog agent]
    WD -->|1| RFT[read_flagged_tracts]
    WD -->|2| ER2[get_existing_resources]
    WD -->|2| ER3[get_rural_existing_resources]
    WD -->|3| CRA[check_resource_appeared]
    WD -->|4| UFT[update_flagged_tract]

    RFT -.reads pending, any type.-> LOG
    ER2 -.queries.-> OSM
    ER3 -.queries.-> OSM
    UFT -->|writes status| LOG
    WD --> REPORT["Recheck summary\n(resolved / still needed)"]

    LOG -.reads all rows.-> IMPACT[compute_impact_metrics]
    IMPACT --> DASH["Impact dashboard\n(dashboard.py, Flask)"]
```

Only `write_evidence_brief` and `write_route_brief` call a language model.
Every other tool — `get_low_access_tracts`, `get_low_access_rural_tracts`,
`get_existing_resources`, `get_rural_existing_resources`, `score_gaps`,
`flag_top_tract_for_recheck`, `flag_top_route_for_recheck`,
`read_flagged_tracts`, `check_resource_appeared`, `update_flagged_tract`,
`compute_impact_metrics` — is deterministic: reading a local database,
calling a public API, doing arithmetic, or writing a validated row. That
split is deliberate: for a tool whose output might end up in a funding
application, "here's the exact formula" is more defensible than "the
model said so." `score_gaps` itself is shared unchanged between both
Advisors — see its use in `route_advisor.py`.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**AWS credentials** (Strands' default model provider is Amazon Bedrock):
run `aws configure`, or copy `.env.example` to `.env` and fill in your keys.
Your credentials need `bedrock:InvokeModel` (and
`bedrock:InvokeModelWithResponseStream` if you use streaming) permission,
and you may need to request model access for Claude in the Bedrock console
first.

### Data setup

Both Advisors run out of the box against small, clearly-labeled sample
tracts (see the docstrings in `tools/access_data.py` — `_sample_tracts`
for the urban pilot city, `_sample_rural_tracts` for the rural pilot
county) so you can smoke-test the plumbing immediately. For real
data:

1. **USDA Food Access Research Atlas — LRAM or SRAM.** USDA discontinued
   the old standalone SNAP Retailer Locator (individual retailer points);
   what replaced it is folded into the Atlas itself as two tract-level
   products, both downloadable from
   <https://www.ers.usda.gov/data-products/food-access-research-atlas/download-the-data>:
   **LRAM** (Large Retailer Access Map — large grocery/supermarkets only,
   2019 data) is the methodological continuation of the classic Atlas and
   the recommended default, since it isolates genuine full-service grocery
   access rather than counting a dollar store as "access." **SRAM**
   (SNAP-authorized Retailer Access Map — every SNAP-authorized store
   including convenience and dollar stores, 2025 data) is more current but
   more lenient. Download either product, save it locally, then run
   `python data/prep_atlas.py --input data/raw/lram.csv --product LRAM --regions all`
   — it matches columns by pattern rather than one hardcoded spelling,
   since names have shifted across releases, and prints what it found if
   auto-matching needs a hand.
2. **OpenStreetMap** — no setup needed. Queried live via the public
   Overpass API in `tools/existing_resources.py` for community gardens,
   grocery stores, farms, and convenience stores (kept separate from real
   grocery stores in scoring — see `tools/gap_scorer.py`, a corner store
   isn't the same access as a supermarket). The rural query additionally
   looks for `social_facility=food_bank` and `amenity=marketplace`.
   Neither LRAM nor SRAM publish individual retailer coordinates, so OSM
   is this project's only point-level resource layer — that's a real gap
   worth knowing about, not a hidden one.
3. **Census TIGER/Line tract boundaries** (for the planning-workspace
   map's real tract polygons — optional, only needed for that UI): run
   `python data/prep_tract_boundaries.py`. See
   [Orchestration, API, and the planning-workspace UI](#orchestration-api-and-the-planning-workspace-ui)
   below for details and a real caveat about this script's verification.

The default `--regions all` run builds both `data/atlas_pilot_city.db` and
`data/atlas_rural_county.db` from the same official file. Use `--regions
urban` or `--regions rural` to build only one. Each database records the
selected product, source file, thresholds, region, and preparation time in
its `metadata` table. Tool responses label fallback records as
`data_mode=sample` and prepared records as `data_mode=real`.

To point the Site Advisor at a different city, or the Route Advisor at a
different rural county, edit `config.py` (county FIPS + bounding box) and
re-run `data/prep_atlas.py`. Nothing else in the project hardcodes a
location — that's the scalability story: the Atlas already covers every
census tract in the country.

## Run it

```bash
python agent.py
```

```
Food-Access Advisor ready — pilot city: Chicago, IL
> where's the highest-need spot for a new food resource?
```

```bash
python route_advisor.py
```

```
Route Advisor ready — rural pilot county: Alexander County, IL
> where would a route change help most?
```

Each answer also flags its top tract in `data/flagged_tracts.db` for the
Watchdog to check later. Run the Watchdog's recheck pass (normally fired on
a schedule, e.g. EventBridge — here run manually) with:

```bash
python watchdog_agent.py
```

```
Food-Access Watchdog — pilot city: Chicago, IL
Running a single unattended recheck pass over the flagged-tracts backlog...
```

It reports how many flagged tracts it checked, how many now have a nearby
resource, and how many are still needed — and updates each row accordingly.
It checks "site" (urban) rows against Chicago's live OSM data at a 1-mile
threshold, and "route" (rural) rows against Alexander County's live OSM
data at a 10-mile threshold — never the wrong region's data for either.

### Impact dashboard

The design canvas's own stated success metric — "unclosed gaps trending
down, per region, not pooled" plus "median days to resolution" — is
computed by `tools/impact_metrics.py` and served as a small live-refreshing
local dashboard:

```bash
python dashboard.py
```

Then open <http://127.0.0.1:5050>. It reads `data/flagged_tracts.db`
directly (no LLM call) and shows, separately for the urban and rural
regions: how many flagged tracts remain unclosed, how many resolved, and
the median days it took to resolve them. Auto-refreshes every 30 seconds,
so leaving it open while running the Watchdog shows the numbers move.

### Orchestration, API, and the planning-workspace UI

Three additive layers sit on top of the three agents above — none of them
change an agent's tools, prompts, or guardrails, and the CLI scripts above
still work unmodified.

**`orchestration.py` — GraphBuilder, honestly scoped.** A real, verified
constraint from Strands' `GraphBuilder` shaped this: a single shared
`Graph` can't do conditional cross-agent routing (`add_node` only accepts
`AgentBase`/`MultiAgentBase` instances, and — confirmed directly against
the installed `strands-agents` package's execution engine — *every* entry
point fires on *every* `graph()` call, with no per-call way to pick just
one). A shared graph containing all three agents would therefore run Site
Advisor, Route Advisor, and Watchdog together on every invocation, which
is wrong here. So `orchestration.py` wraps each agent in its own trivial
single-node `Graph` (`build_site_graph()`, `build_route_graph()`,
`build_watchdog_graph()`) and adds one plain-Python dispatcher,
`route_request(mode, question)` — `mode` is an explicit field the caller
supplies, never inferred by a model, matching this project's existing
discipline of keeping region/mode selection out of anything the model
could get wrong. The honest value this adds is a uniform `GraphResult`
and graph-level timeout/session plumbing across all three agents — not
new routing logic, which stays exactly the plain `if` it always was.

**`api/` — a FastAPI backend.** `POST /api/site-advisor` and
`POST /api/route-advisor` call `orchestration.route_request` (async, via
`asyncio.to_thread`, so a slow agent call doesn't block the event loop);
`GET /api/flagged-tracts` and `GET /api/impact-metrics` wrap
`tools/flagged_tracts.py` and `tools/impact_metrics.py` directly;
`GET /api/tract-boundaries` serves the GeoJSON `data/prep_tract_boundaries.py`
produces. Run it with:

```bash
uvicorn api.main:app --reload
```

Advisor calls are the slow path (a full tool-calling loop plus at least
one Bedrock round trip) — `model.py`'s `streaming=False` is a deliberate,
already-tested fix for a real `ReadTimeoutError` this project hit earlier,
so these endpoints don't attempt token-by-token SSE streaming; expect
several seconds to over a minute per call, not a sub-second REST response.

**`frontend/` — a React + MapLibre planning workspace.** A browser UI
replacing "three command-line scripts" for the demo: an overview map
toggling between the two pilot regions (tract polygons colored by
flagged-tract status), Site Advisor / Route Advisor workspaces (a chat-style
question box against their respective endpoints), and a Follow-up page
reusing `/api/impact-metrics`' per-region breakdown (the same data
`dashboard.py` renders server-side, here as a React table). Run it with:

```bash
cd frontend
npm install
cp .env.example .env   # point VITE_API_BASE_URL at your running API if not localhost:8000
npm run dev
```

Two things worth knowing before you rely on this for a demo:

- **v1 renders the advisor's answer as one evidence-panel paragraph, not
  a clickable ranked table.** The Advisor endpoints return the agent's
  composed text (the ranked recommendation plus the cited brief, exactly
  as `write_evidence_brief`/`write_route_brief` produce it) — not
  `score_gaps`'s structured per-tract JSON, since no endpoint exposes that
  separately today. A structured ranking endpoint is a natural fast-follow.
- **The map's real tract-polygon rendering has not been visually verified
  by this project's own testing.** `data/prep_tract_boundaries.py`
  (Census TIGER/Line shapefiles → GeoJSON) couldn't be run end-to-end from
  the sandbox that wrote it — `census.gov` is blocked by that sandbox's
  network egress policy, the same pattern hit earlier with
  `docs.aws.amazon.com`. Its GeoJSON-conversion/join logic was verified
  against a synthetic shapefile (confirmed correct filtering and a correct
  "N tracts missing a boundary" warning), and `HomeMap.tsx`'s
  `addSource`/`addLayer` calls were confirmed to receive well-formed
  GeoJSON with zero runtime errors — but the actual visual result (and the
  real Census download) needs a run in a normal networked environment
  with real GPU/WebGL, i.e. your machine, not this sandbox's headless one.

## Test it

```bash
pytest
```

`tools/gap_scorer.py`, `tools/recheck_status.py`, `tools/flagged_tracts.py`,
`tools/impact_metrics.py`, `tools/telemetry.py`, `orchestration.py`,
`api/main.py`, and the deterministic parts of `tools/access_data.py` /
`tools/existing_resources.py` are pure Python (plus SQLite for the log)
with no AWS dependency — all tested directly against temp databases,
mocked HTTP calls, a fake `StrandsTelemetry`, or (for the API) FastAPI's
own `TestClient` with the advisor calls mocked — no credentials, no live
network required.

### Tracing and metrics (opt-in)

All three entrypoints call `tools/telemetry.py`'s `configure_telemetry()`
at startup, which is a real no-op unless you set one of:

```bash
export STRANDS_TELEMETRY_CONSOLE=1        # print spans + event-loop metrics
export OTEL_EXPORTER_OTLP_ENDPOINT=<url>  # also export to an OTLP collector
```

With `STRANDS_TELEMETRY_CONSOLE` set, each run additionally prints its
event-loop metrics (latency, token counts, tool-call counts) via Strands'
own `metrics_to_string` — useful for actually seeing where a slow run's
time went (model call vs. a specific tool) instead of guessing. OTLP
export needs `opentelemetry-exporter-otlp-proto-http` installed
separately; it's not in `requirements.txt` since it's unused unless you
opt in.

### Running the Watchdog on Bedrock AgentCore Runtime

`watchdog_agent.py`'s manual run above is the local/test path. To actually
host the Watchdog as the scheduled service the architecture diagram above
shows, `watchdog_agentcore_entry.py` wraps the same `build_watchdog()` in a
`BedrockAgentCoreApp` for Amazon Bedrock AgentCore Runtime:

```bash
agentcore configure --entrypoint watchdog_agentcore_entry.py --requirements-file requirements.txt
agentcore deploy
agentcore invoke '{"prompt": "Run today’s recheck pass over every pending flagged tract."}'
```

`agentcore deploy` builds an ARM64 container in the cloud via CodeBuild and
hosts it on AgentCore Runtime — no local Docker required. These commands
come from the `bedrock-agentcore-starter-toolkit` package (added to
`requirements.txt`); run `agentcore --help` to confirm current flags before
deploying, since AWS is actively evolving this tooling. This deploy step
needs your own AWS credentials and hasn't been run as part of this repo —
`watchdog_agentcore_entry.py` is verified-correct code, not a live
deployment.

Once deployed, **[`deploy/EVENTBRIDGE_SETUP.md`](deploy/EVENTBRIDGE_SETUP.md)**
covers actually putting it on a recurring schedule: an EventBridge
Scheduler rule, a small Lambda shim (`deploy/watchdog_scheduler_lambda.py`),
and why the shim is needed rather than pointing Scheduler at AgentCore
directly (Scheduler's direct invoke is synchronous and times out around
30 seconds — well short of a full recheck pass). Like the deploy step
above, this needs your own AWS credentials to actually run.

## Guardrails

- **Stay-in-the-pilot-region is enforced in code, not just in the prompt,
  for both Advisors.** `get_low_access_tracts` / `get_existing_resources`
  take no city/region/bounding-box arguments at all — both always resolve
  to `config.PILOT_CITY`. `get_low_access_rural_tracts` /
  `get_rural_existing_resources` are pinned the same way to
  `config.PILOT_RURAL_COUNTY`. An earlier version accepted a `city` string
  and free-form bounding-box floats that were never actually validated
  against anything, which meant the only thing stopping the model from
  answering for the wrong region was the system prompt asking it nicely.
  That's fixed: there's no argument left for the model (or a bug) to
  misuse, so the boundary holds even if the prompt is ignored, edited, or
  the model just gets it wrong. The system prompt still tells each agent
  to *say* when a question is out of scope — that's a wording/UX
  instruction now, not the only thing enforcing the boundary.
- **Site Advisor, Route Advisor, and Watchdog are three separate agents
  with disjoint tool lists, not one agent with a mode flag — and
  `orchestration.py`'s `GraphBuilder` wrapping doesn't change that.**
  Neither Advisor has a tool that can write to a flagged tract's status;
  the Watchdog has no tool that can answer a siting or routing question or
  make a new recommendation. `flag_top_tract_for_recheck` (Site Advisor's
  only write) hardcodes `recommendation_type="site"` and
  `source_agent="advisor"`; `flag_top_route_for_recheck` (Route Advisor's
  only write) hardcodes `recommendation_type="route"` and
  `source_agent="route_advisor"` — neither is a model-settable argument,
  so neither Advisor can mislabel a row as coming from the other.
  `update_flagged_tract` (the Watchdog's only write) takes a fixed,
  validated status enum and writes to exactly one table — there's no
  table-name or raw-SQL argument for a model to misuse. `route_request`'s
  `mode` argument is likewise a plain caller-supplied string, never
  something a model infers.
- **The Route Advisor's capacity trade-off is a required disclosure, not
  optional color.** A mobile route or delivery day has fixed stop
  capacity, so "add a stop here" is usually really "move a stop from
  somewhere else" — a Success-to-the-Successful risk found during the
  rural systems-thinking pass (see `docs/design-canvas.html`).
  `write_route_brief`'s own system prompt requires naming this trade-off
  explicitly in every brief; it isn't left to the orchestrator prompt to
  remember, since a content rule worth actually testing needs to live
  where the sentence a reader sees is actually generated. The frontend's
  Route Advisor workspace renders this paragraph in full, not truncated.
- **The Watchdog checks each flagged tract against the resources and
  distance threshold that actually match its region.** A "site" row is
  checked against `get_existing_resources` (urban) at the 1-mile default;
  a "route" row is checked against `get_rural_existing_resources` (rural)
  at `RURAL_NEARBY_THRESHOLD_MILES` (10 miles) — never the other region's
  data, which would silently compare a tract to resources roughly 180
  miles away and always report "still needed" regardless of what actually
  opened nearby.
- Every answer names the USDA Food Access Research Atlas and its
  publication vintage.
- Every output is framed as decision support, not a decision — a human
  still chooses where to act.
- No personal data is involved anywhere in this pipeline — every dataset
  here is public, tract- or retailer-level, not individual-level.

## Roadmap

- **Watchdog on a real recurring schedule.** `watchdog_agentcore_entry.py`
  is deployable today; [`deploy/EVENTBRIDGE_SETUP.md`](deploy/EVENTBRIDGE_SETUP.md)
  now documents the EventBridge Scheduler + Lambda shim needed to actually
  invoke the deployed AgentCore Runtime endpoint on a cadence, making the
  architecture diagram's "Scheduled trigger" real instead of manual. Still
  needs the deploy and schedule creation to actually be run with real AWS
  credentials — that's a one-time setup step for whoever operates this,
  not something this repo can do on its own.
- **A structured ranked-tract endpoint for the frontend.** The planning
  workspace currently renders each Advisor's composed text answer as one
  evidence panel (see [above](#orchestration-api-and-the-planning-workspace-ui));
  a `score_gaps`-shaped JSON endpoint would let the UI render a real
  clickable ranked table instead.
- **Verify the real tract-polygon map rendering and Census download.**
  `data/prep_tract_boundaries.py` and `HomeMap.tsx`'s data pipeline were
  verified as far as this project's sandboxed, network-restricted testing
  environment allowed (see above) — running the actual download and
  looking at the actual rendered map is the next step, in a normal
  environment.
- **Transit-time distance.** Straight-line miles (what's implemented now)
  understates real access — a tract "0.6 miles" from a grocery store can be
  a 40-minute bus ride away. Swapping in a GTFS feed + a routing engine
  (e.g. OSRM) for the pilot city is the single biggest accuracy upgrade
  available, and the reason this project cites transit-time distance as a
  stretch goal rather than shipping it as a guess.
- **Real rural Atlas data.** The Route Advisor's code is built and tested,
  but `data/prep_atlas.py` doesn't yet build `data/atlas_rural_county.db`
  from a real LRAM/SRAM download for Alexander County, IL — it runs on
  illustrative sample data until that's done (see Data setup above).
- **Trend forecasting.** A model trained across multiple Atlas vintages to
  flag tracts trending toward low-access before they're fully flagged.
  Academic precedent already exists for this technique, so it's the lowest
  differentiation-per-effort of the remaining items — cut first if time is
  short.

## Design docs

The systems-thinking design work behind this project (stocks/flows, the
broken loop, leverage point, guardrail rationale) lives in
[`docs/design-canvas.html`](docs/design-canvas.html) — a static export of
the working design canvas. [`docs/project-tracker.html`](docs/project-tracker.html)
is a point-in-time export of the build tracker; it's a snapshot, not a
live document, since the tracker keeps evolving after each export.

GitHub's file viewer shows these as source, not rendered pages — download
them and open locally in a browser, or enable **GitHub Pages** (Settings →
Pages → Deploy from a branch → `/docs`) to get them served as real pages
at `https://explainable-ai.github.io/food-access-advisor/design-canvas.html`
and `.../project-tracker.html`.

## License

MIT — see [LICENSE](LICENSE).
