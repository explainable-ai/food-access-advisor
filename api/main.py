"""FastAPI backend for the planning-workspace frontend.

Thin wrapper only -- every handler here calls straight into existing,
already-tested code (orchestration.route_request, tools.flagged_tracts,
tools.impact_metrics). No business logic, no duplicated tool lists,
prompts, or guardrails live in this file.

Advisor calls are the slow path: several tool calls plus at least one
Bedrock round trip (model.py pins streaming=False -- a deliberate, tested
fix for a real ReadTimeoutError this project hit earlier -- so this
does not attempt token-by-token SSE streaming to the browser; it would
re-litigate an already-settled tradeoff for uneven benefit). Handlers are
`async def` and run the underlying synchronous `route_request` call in a
thread via `asyncio.to_thread`, so FastAPI's event loop isn't blocked
while an Advisor call runs -- but the response only comes back once the
whole call completes, realistically anywhere from several seconds to over
a minute (Overpass alone retries up to tools.existing_resources.MAX_ATTEMPTS
times with tools.existing_resources.RETRY_DELAY_SECONDS between attempts).
A frontend calling these endpoints should use a generous client-side
timeout (60-120s) and show a loading state, not a spinner tuned for a
sub-second REST call.
"""

import asyncio

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api.schemas import AdvisorRequest, AdvisorResponse, ImpactMetrics
from orchestration import route_request
from tools.existing_resources import OverpassQueryError
from tools.flagged_tracts import ALLOWED_STATUSES, read_flagged_tracts
from tools.impact_metrics import compute_impact_metrics

app = FastAPI(title="Food-Access Advisor API")

# Wide open for local development against a separately-run React dev
# server (different origin/port). Narrow this to the frontend's real
# origin before deploying anywhere reachable from the public internet.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _answer_text(graph_result, node_id: str) -> str:
    """Pull the underlying agent's plain-text answer out of a GraphResult.

    graph_result.results[node_id].result is the single node's AgentResult
    for these one-node graphs; str(AgentResult) already extracts and joins
    its text content blocks (see strands.agent.agent_result.AgentResult).
    """
    return str(graph_result.results[node_id].result)


async def _run_advisor(mode: str, node_id: str, question: str) -> AdvisorResponse:
    try:
        result = await asyncio.to_thread(route_request, mode, question)
    except OverpassQueryError as exc:
        raise HTTPException(status_code=502, detail=f"OpenStreetMap query failed: {exc}") from exc
    except Exception as exc:  # Bedrock/model errors, etc. -- surface a real message, not a bare 500
        raise HTTPException(status_code=502, detail=f"{mode} advisor failed: {exc}") from exc
    return AdvisorResponse(answer=_answer_text(result, node_id))


@app.post("/api/site-advisor", response_model=AdvisorResponse)
async def site_advisor(request: AdvisorRequest) -> AdvisorResponse:
    return await _run_advisor("site", "site_advisor", request.question)


@app.post("/api/route-advisor", response_model=AdvisorResponse)
async def route_advisor_endpoint(request: AdvisorRequest) -> AdvisorResponse:
    return await _run_advisor("route", "route_advisor", request.question)


@app.get("/api/flagged-tracts")
def flagged_tracts(status: str = Query(default="pending")):
    if status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown status {status!r}; expected one of {ALLOWED_STATUSES}",
        )
    return read_flagged_tracts(status)


@app.get("/api/impact-metrics", response_model=ImpactMetrics)
def impact_metrics() -> ImpactMetrics:
    return compute_impact_metrics()
