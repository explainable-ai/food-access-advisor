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
import json
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api.schemas import (
    AdvisorRequest,
    AdvisorResponse,
    EvidenceRequest,
    EvidenceReviewRequest,
    EvidenceResponse,
    ExistingResource,
    ImpactMetrics,
    RankedTract,
    RoadRoute,
    RouteDirectionsRequest,
    RouteOptimizationRequest,
    RouteOptimizationResponse,
    VerifyRequest,
)
from api.auth import require_staff_user
from config import PILOT_CITY, PILOT_RURAL_COUNTY
from orchestration import route_request
from tools.access_data import (
    PreparedTractDataError,
    get_all_tracts,
    get_low_access_rural_tracts,
    get_low_access_tracts,
)
from tools.evidence_brief import write_evidence_brief, write_route_brief
from tools.evidence_snapshots import read_change_page, review_change
from tools.existing_resources import OverpassQueryError
from tools.flagged_tracts import ALLOWED_STATUSES, read_flagged_tracts, verify_flagged_tract
from tools.gap_scorer import score_all_gaps, score_gaps
from tools.impact_metrics import compute_impact_metrics
from tools.route_optimizer import optimize_route
from tools.resource_cache import ResourceCacheError, load_resource_cache
from tools.travel_time_provider import (
    TravelTimeProviderError,
    get_openrouteservice_directions,
    get_openrouteservice_matrix,
)

DATA_DIR = Path(__file__).parent.parent / "data"
# Produced by data/prep_tract_boundaries.py -- see that script for why
# these are separate one-time-generated files rather than computed here.
BOUNDARY_FILES_BY_COUNTY_FIPS = {
    PILOT_CITY["county_fips"][0]: DATA_DIR / "tract_boundaries_pilot_city.geojson",
    PILOT_RURAL_COUNTY["county_fips"][0]: DATA_DIR / "tract_boundaries_rural_county.geojson",
}

app = FastAPI(title="Food-Access Advisor API")


def _cors_origins() -> list[str]:
    """Return explicit origins only; a wildcard is never accepted."""
    raw = os.getenv("FOOD_ACCESS_CORS_ORIGINS", "http://localhost:5173")
    origins = [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    if "*" in origins:
        raise RuntimeError("FOOD_ACCESS_CORS_ORIGINS must contain explicit origins, never '*'")
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)


@app.get("/health")
def health():
    """Load-balancer health check; deliberately performs no paid/network calls."""
    return {"status": "ok"}


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


def _ranked_tracts(get_tracts, get_resources, top_n: int, weights=None) -> list:
    """Deterministic ranking -- the same three calls the corresponding
    Advisor agent makes (get_*_tracts -> get_*_resources -> score_gaps),
    just without the LLM step. No Bedrock call, no added latency; only
    get_*_resources touches the network (live Overpass), so this can still
    raise OverpassQueryError."""
    tracts = get_tracts()
    resources = get_resources()
    return score_gaps(tracts, resources, top_n=top_n, weights=weights)


def _weights(**values):
    supplied = {name: value for name, value in values.items() if value is not None}
    if supplied and not any(value > 0 for value in supplied.values()):
        raise HTTPException(status_code=422, detail="At least one priority weight must be greater than zero")
    return supplied or None


@app.get("/api/site-advisor/ranked-tracts", response_model=list[RankedTract])
def site_ranked_tracts(top_n: int = Query(default=3, ge=1, le=100),
                       food_access_gap: float | None = Query(default=None, ge=0),
                       poverty: float | None = Query(default=None, ge=0),
                       no_vehicle: float | None = Query(default=None, ge=0),
                       population_served: float | None = Query(default=None, ge=0),
                       transit_burden: float | None = Query(default=None, ge=0),
                       existing_coverage: float | None = Query(default=None, ge=0)):
    try:
        weights = _weights(food_access_gap=food_access_gap, poverty=poverty, no_vehicle=no_vehicle,
                           population_served=population_served, transit_burden=transit_burden,
                           existing_coverage=existing_coverage)
        return _ranked_tracts(get_low_access_tracts, lambda: load_resource_cache("urban"), top_n, weights)
    except ResourceCacheError as exc:
        raise HTTPException(status_code=503, detail=f"Prepared resource data unavailable: {exc}") from exc


@app.get("/api/site-advisor/tract-scores", response_model=list[RankedTract])
def site_tract_scores(food_access_gap: float | None = Query(default=None, ge=0),
                      poverty: float | None = Query(default=None, ge=0),
                      no_vehicle: float | None = Query(default=None, ge=0),
                      population_served: float | None = Query(default=None, ge=0),
                      transit_burden: float | None = Query(default=None, ge=0),
                      existing_coverage: float | None = Query(default=None, ge=0)):
    """Return an evidence score for every prepared Cook County tract.

    The endpoint reads only deployment-prepared Atlas/ACS and resource-cache
    snapshots. It never calls Overpass at request time and never fills missing
    tracts with illustrative values.
    """
    try:
        weights = _weights(food_access_gap=food_access_gap, poverty=poverty, no_vehicle=no_vehicle,
                           population_served=population_served, transit_burden=transit_burden,
                           existing_coverage=existing_coverage)
        return score_all_gaps(get_all_tracts(), load_resource_cache("urban"), weights=weights)
    except (PreparedTractDataError, ResourceCacheError) as exc:
        raise HTTPException(status_code=503, detail=f"Prepared heatmap data unavailable: {exc}") from exc


@app.get("/api/route-advisor/ranked-tracts", response_model=list[RankedTract])
def route_ranked_tracts(top_n: int = Query(default=3, ge=1, le=100),
                        food_access_gap: float | None = Query(default=None, ge=0),
                        poverty: float | None = Query(default=None, ge=0),
                        no_vehicle: float | None = Query(default=None, ge=0),
                        population_served: float | None = Query(default=None, ge=0),
                        transit_burden: float | None = Query(default=None, ge=0),
                        existing_coverage: float | None = Query(default=None, ge=0)):
    try:
        weights = _weights(food_access_gap=food_access_gap, poverty=poverty, no_vehicle=no_vehicle,
                           population_served=population_served, transit_burden=transit_burden,
                           existing_coverage=existing_coverage)
        return _ranked_tracts(get_low_access_rural_tracts, lambda: load_resource_cache("rural"), top_n, weights)
    except ResourceCacheError as exc:
        raise HTTPException(status_code=503, detail=f"Prepared resource data unavailable: {exc}") from exc


@app.get("/api/site-advisor/resources", response_model=list[ExistingResource])
def site_resources():
    try:
        return load_resource_cache("urban")
    except ResourceCacheError as exc:
        raise HTTPException(status_code=503, detail=f"Prepared resource data unavailable: {exc}") from exc


@app.get("/api/route-advisor/resources", response_model=list[ExistingResource])
def route_resources():
    try:
        return load_resource_cache("rural")
    except ResourceCacheError as exc:
        raise HTTPException(status_code=503, detail=f"Prepared resource data unavailable: {exc}") from exc


@app.post("/api/route-advisor/optimize", response_model=RouteOptimizationResponse)
def optimize_route_scenario(request: RouteOptimizationRequest):
    """Run deterministic route selection; no Bedrock or network call."""
    try:
        matrix = request.travel_time_matrix
        source = None
        if matrix is None and request.travel_time_provider == "openrouteservice":
            points = [request.depot.model_dump(), *[candidate.model_dump() for candidate in request.candidates]]
            matrix = get_openrouteservice_matrix(points)
            source = "openrouteservice_matrix"
        return optimize_route(
            candidates=[candidate.model_dump() for candidate in request.candidates],
            depot=request.depot.model_dump(), max_route_minutes=request.max_route_minutes,
            vehicle_capacity=request.vehicle_capacity, max_stops=request.max_stops,
            service_minutes=request.service_minutes, travel_time_matrix=matrix,
            average_speed_mph=request.average_speed_mph, travel_time_source=source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TravelTimeProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/route-advisor/directions", response_model=RoadRoute)
def route_directions(request: RouteDirectionsRequest):
    """Return road geometry and instructions without exposing the provider key."""
    points = [
        request.origin.model_dump(),
        *[point.model_dump() for point in request.waypoints],
        request.destination.model_dump(),
    ]
    try:
        return get_openrouteservice_directions(points, alternatives=request.alternatives)
    except (ValueError, TravelTimeProviderError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _run_evidence(write_brief_fn, tract: RankedTract) -> EvidenceResponse:
    """A single Bedrock call (one tiny sub-agent, see tools/evidence_brief.py)
    -- not the multi-tool agent loop POST /api/*-advisor runs. Fast enough
    that a frontend can call it when a user clicks one ranked-table row,
    distinct from the slower free-text "Ask the Advisor" box."""
    try:
        brief = await asyncio.to_thread(write_brief_fn, tract.model_dump())
    except Exception as exc:  # Bedrock/model errors -- surface a real message, not a bare 500
        raise HTTPException(status_code=502, detail=f"evidence brief failed: {exc}") from exc
    return EvidenceResponse(brief=brief)


@app.post("/api/site-advisor/evidence", response_model=EvidenceResponse)
async def site_evidence(request: EvidenceRequest) -> EvidenceResponse:
    return await _run_evidence(write_evidence_brief, request.tract)


@app.post("/api/route-advisor/evidence", response_model=EvidenceResponse)
async def route_evidence(request: EvidenceRequest) -> EvidenceResponse:
    return await _run_evidence(write_route_brief, request.tract)


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


@app.get("/api/watchdog/changes")
def watchdog_changes(limit: int = Query(default=10, ge=1, le=100), cursor: str | None = None,
                     source_id: str | None = None, status: str = Query(default="open")):
    """Return deduplicated, cursor-paginated findings for human review."""
    try:
        return read_change_page(limit=limit, cursor=cursor, source_id=source_id, status=status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/watchdog/changes/review")
def review_watchdog_change(request: EvidenceReviewRequest,
                           staff_user: dict[str, Any] = Depends(require_staff_user)):
    """Record a staff review without silently changing a ranking or route."""
    reviewed_by = str(
        staff_user.get("email") or staff_user.get("username")
        or staff_user.get("cognito:username") or staff_user.get("sub")
    )
    result = review_change(
        source_scope=request.source_scope,
        record_key=request.record_key,
        action=request.action,
        reviewed_by=reviewed_by,
        note=request.note or "",
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return {
        "status": "review_recorded",
        "message": "Review recorded. No site ranking or route was changed automatically.",
        "finding": result,
    }


@app.post("/api/flagged-tracts/verify")
def verify_tract(request: VerifyRequest,
                 _staff_user: dict[str, Any] = Depends(require_staff_user)):
    """A human's verification of a Watchdog-observed 'possible_change' --
    see tools/flagged_tracts.py's verify_flagged_tract for the four
    verification choices and what each maps to."""
    result = verify_flagged_tract(
        request.tract_fips, request.recommendation_type, request.verification, note=request.note or ""
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/tract-boundaries")
def tract_boundaries(county: str = Query(..., description="County FIPS, e.g. 17031 or 17003")):
    """Serves the GeoJSON FeatureCollection built by
    data/prep_tract_boundaries.py -- one file per pilot region, keyed by
    tract_fips. Returns 404 with a clear message (not a bare file-not-found
    crash) if that prep script hasn't been run yet."""
    boundary_path = BOUNDARY_FILES_BY_COUNTY_FIPS.get(county)
    if boundary_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"No tract boundaries configured for county {county!r}; "
            f"expected one of {list(BOUNDARY_FILES_BY_COUNTY_FIPS)}",
        )
    if not boundary_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"{boundary_path.name} doesn't exist yet -- run "
            "`python data/prep_tract_boundaries.py` first.",
        )
    return json.loads(boundary_path.read_text())
