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
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api.operations_read import router as operations_read_router

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
    get_all_rural_tracts,
    get_all_tracts,
    get_low_access_rural_tracts,
)
from tools.evidence_brief import write_route_brief
from tools.evidence_snapshots import read_change_page, review_change
from data_sources.community_sources import source_registry
from services.direct_source_signals import refresh_direct_sources
from tools.existing_resources import OverpassQueryError
from tools.flagged_tracts import ALLOWED_STATUSES, read_flagged_tracts, verify_flagged_tract
from tools.gap_scorer import DEFAULT_WEIGHTS, score_all_gaps, score_gaps
from tools.impact_metrics import compute_impact_metrics
from tools.route_optimizer import optimize_route
from tools.resource_cache import ResourceCacheError, load_resource_cache
from tools.site_evidence_brief import write_site_evidence_brief
from tools.travel_time_provider import (
    TravelTimeProviderError,
    get_openrouteservice_directions,
    get_openrouteservice_matrix,
)

DATA_DIR = Path(__file__).parent.parent / "data"
# Produced by data/prep_tract_boundaries.py -- see that script for why
# these are separate one-time-generated files rather than computed here.
BOUNDARY_FILES_BY_COUNTY_FIPS = {
    **{
        county: DATA_DIR / "tract_boundaries_pilot_city.geojson"
        for county in PILOT_CITY["county_fips"]
    },
    **{
        county: DATA_DIR / "tract_boundaries_rural_county.geojson"
        for county in PILOT_RURAL_COUNTY["county_fips"]
        if county not in PILOT_CITY["county_fips"]
    },
}

app = FastAPI(title="Food-Access Advisor API")
app.include_router(operations_read_router)


def _cors_origins() -> list[str]:
    """Return explicit local/custom origins; a wildcard is never accepted."""
    raw = os.getenv(
        "FOOD_ACCESS_CORS_ORIGINS",
        (
            "http://localhost:5173,http://localhost:8080,"
            "https://preview--food-equity-navigator.lovable.app,"
            "https://food-equity-navigator.lovable.app,https://food-guide-advisor.lovable.app,"
            "https://4edb7a89-80c4-4184-bc69-eb628ea0e136.lovableproject.com,"
            # Lovable's in-editor live preview iframe uses this domain
            # (id-preview--<project-id>.lovable.app), distinct from the
            # published-preview domain above (same project ID, different
            # host) -- both are needed, not just one.
            "https://id-preview--4edb7a89-80c4-4184-bc69-eb628ea0e136.lovable.app"
        ),
    )
    origins = [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    if "*" in origins:
        raise RuntimeError("FOOD_ACCESS_CORS_ORIGINS must contain explicit origins, never '*'")
    return origins


def _cors_origin_regex() -> str | None:
    """Return only an explicitly configured regex; exact origins are safer by default."""
    raw = os.getenv("FOOD_ACCESS_CORS_ORIGIN_REGEX", "")
    return raw.strip() or None


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=_cors_origin_regex(),
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


def _weights(_forced=None, **values):
    supplied = {name: value for name, value in values.items() if value is not None}
    forced = _forced or {}
    if not supplied and not forced:
        return None
    effective = {**asdict(DEFAULT_WEIGHTS), **supplied, **forced}
    if not any(value > 0 for value in effective.values()):
        raise HTTPException(status_code=422, detail="At least one priority weight must be greater than zero")
    return {**supplied, **forced}


def _site_weights(study_area, **values):
    """Use one comparable weight set across every tract in a study area."""
    transit = values.get("transit_burden")
    if study_area == "cook_county" and transit not in (None, 0):
        raise HTTPException(
            status_code=422,
            detail="Transit burden is available only for Chicago; use a zero transit weight for Cook County.",
        )
    if study_area == "cook_county":
        values.pop("transit_burden", None)
        return _weights(_forced={"transit_burden": 0}, **values)
    return _weights(**values)


def _urban_study_area(tracts: list, study_area: Literal["chicago", "cook_county"]):
    """Keep neighborhood-first Chicago ranking distinct from county context."""
    if study_area == "chicago":
        return [tract for tract in tracts if tract.get("is_chicago")]
    return tracts


@app.get("/api/site-advisor/ranked-tracts", response_model=list[RankedTract])
def site_ranked_tracts(top_n: int = Query(default=3, ge=1, le=100),
                       study_area: Literal["chicago", "cook_county"] = "chicago",
                       food_access_gap: float | None = Query(default=None, ge=0),
                       poverty: float | None = Query(default=None, ge=0),
                       no_vehicle: float | None = Query(default=None, ge=0),
                       population_served: float | None = Query(default=None, ge=0),
                       transit_burden: float | None = Query(default=None, ge=0),
                       existing_coverage: float | None = Query(default=None, ge=0)):
    try:
        weights = _site_weights(
            study_area,
            food_access_gap=food_access_gap,
            poverty=poverty,
            no_vehicle=no_vehicle,
            population_served=population_served,
            transit_burden=transit_burden,
            existing_coverage=existing_coverage,
        )
        tracts = _urban_study_area(get_all_tracts(), study_area)
        resources = load_resource_cache("urban", require_complete_coverage=True)
        return score_gaps(tracts, resources, top_n=top_n, weights=weights)
    except (PreparedTractDataError, ResourceCacheError) as exc:
        raise HTTPException(status_code=503, detail=f"Prepared resource data unavailable: {exc}") from exc


@app.get("/api/site-advisor/tract-scores", response_model=list[RankedTract])
def site_tract_scores(food_access_gap: float | None = Query(default=None, ge=0),
                      study_area: Literal["chicago", "cook_county"] = "chicago",
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
        weights = _site_weights(
            study_area,
            food_access_gap=food_access_gap,
            poverty=poverty,
            no_vehicle=no_vehicle,
            population_served=population_served,
            transit_burden=transit_burden,
            existing_coverage=existing_coverage,
        )
        resources = load_resource_cache("urban", require_complete_coverage=True)
        tracts = _urban_study_area(get_all_tracts(), study_area)
        return score_all_gaps(tracts, resources, weights=weights)
    except (PreparedTractDataError, ResourceCacheError) as exc:
        raise HTTPException(status_code=503, detail=f"Prepared heatmap data unavailable: {exc}") from exc


@app.get("/api/route-advisor/tract-scores", response_model=list[RankedTract])
def route_tract_scores(food_access_gap: float | None = Query(default=None, ge=0),
                       poverty: float | None = Query(default=None, ge=0),
                       no_vehicle: float | None = Query(default=None, ge=0),
                       population_served: float | None = Query(default=None, ge=0),
                       transit_burden: float | None = Query(default=None, ge=0),
                       existing_coverage: float | None = Query(default=None, ge=0)):
    """Return an evidence score for all 72 prepared rural-fringe tracts.

    Route candidate ranking intentionally filters to tracts with a positive
    rural access gap. The heatmap must use the complete prepared tract universe
    so operators can see the full geographic contrast rather than three dots.
    """
    try:
        weights = _weights(food_access_gap=food_access_gap, poverty=poverty, no_vehicle=no_vehicle,
                           population_served=population_served, transit_burden=transit_burden,
                           existing_coverage=existing_coverage)
        resources = load_resource_cache("rural", require_complete_coverage=True)
        return score_all_gaps(get_all_rural_tracts(), resources, weights=weights)
    except (PreparedTractDataError, ResourceCacheError) as exc:
        raise HTTPException(status_code=503, detail=f"Prepared rural heatmap data unavailable: {exc}") from exc


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
    except (PreparedTractDataError, ResourceCacheError) as exc:
        raise HTTPException(status_code=503, detail=f"Prepared route data unavailable: {exc}") from exc


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
    return await _run_evidence(write_site_evidence_brief, request.tract)


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


@app.get("/api/community-signals/sources")
def community_signal_sources():
    """List the reviewed first-party sources used by Community Access Watch."""
    return source_registry()


@app.post("/api/community-signals/refresh")
def refresh_community_signals(
    _staff_user: dict[str, Any] = Depends(require_staff_user),
):
    """Refresh approved official pages without changing an operational plan."""
    try:
        return refresh_direct_sources()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
def tract_boundaries(county: str = Query(..., description="Configured Illinois county FIPS")):
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

    boundary_data = json.loads(boundary_path.read_text())
    features = boundary_data.get("features")

    if not isinstance(features, list):
        raise HTTPException(
            status_code=500,
            detail=f"{boundary_path.name} is not a valid GeoJSON FeatureCollection.",
        )

    filtered_features = [
        feature
        for feature in features
        if str(feature.get("properties", {}).get("tract_fips", ""))[:5] == county
    ]

    return {
        **boundary_data,
        "features": filtered_features,
    }
