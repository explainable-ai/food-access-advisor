"""Pydantic request/response models for the FastAPI backend.

Field names deliberately mirror the existing tool return shapes verbatim
(tools/flagged_tracts.py's row, tools/impact_metrics.py's per-region dict,
tools/gap_scorer.py's score_gaps output) rather than inventing a new
API-specific shape -- one less thing to keep in sync as those tools evolve.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

FlaggedTractStatus = Literal["pending", "possible_change", "still_needed", "resource_found"]

# The planning-workspace Follow-up page's four human verification actions --
# see tools/flagged_tracts.py's VERIFICATION_STATUS_MAP for what each maps to.
VerificationChoice = Literal["verified_open", "planned_not_open", "incorrect_record", "unrelated"]
EvidenceReviewAction = Literal["acknowledged", "verification_requested", "no_decision_impact"]


class AdvisorRequest(BaseModel):
    question: str


class AdvisorResponse(BaseModel):
    answer: str


class BriefRequest(BaseModel):
    request: str = Field(min_length=1)
    study_area: Optional[Literal["chicago_neighborhoods", "rural_fringe"]] = None


class FeedbackRequest(BaseModel):
    tract_id: str = Field(min_length=1)
    households_served: int = Field(ge=0)


class ScoreContribution(BaseModel):
    component: str
    weight: float
    raw_value: Optional[float] = None
    contribution: Optional[float] = None
    source: str


class FlaggedTract(BaseModel):
    id: Optional[int] = None
    tract_fips: str
    recommendation_type: str
    source_agent: str
    population: Optional[int] = None
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    flagged_date: str
    status: FlaggedTractStatus
    last_checked_date: Optional[str] = None
    note: Optional[str] = None


class RegionMetrics(BaseModel):
    total_flagged: int
    unclosed: int
    possible_change: int
    resolved: int
    median_days_to_resolution: Optional[float] = None
    tracts: list[FlaggedTract]


class ImpactMetrics(BaseModel):
    urban: RegionMetrics
    rural: RegionMetrics


class RankedTract(BaseModel):
    """Matches tools/gap_scorer.py's score_gaps() output exactly -- the
    deterministic ranked-tracts endpoints return this with no LLM call."""

    tract_fips: str
    population: Optional[int] = None
    low_access_half_mile: Optional[int] = None
    low_access_one_mile: Optional[int] = None
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    need_score: float
    rank: Optional[int] = None
    poverty_universe: Optional[float] = None
    population_below_poverty: Optional[float] = None
    households_total: Optional[float] = None
    households_no_vehicle: Optional[float] = None
    community_area: Optional[str] = None
    is_chicago: Optional[bool] = None
    food_insecurity_rate: Optional[float] = None
    food_insecurity_population: Optional[float] = None
    food_insecurity_universe: Optional[float] = None
    transit_nearest_stop_miles: Optional[float] = None
    transit_route_count: Optional[int] = None
    transit_weekday_trips: Optional[float] = None
    scoring_context_version: Optional[str] = None
    score_components: dict[str, Optional[float]] = Field(default_factory=dict)
    score_contributions: dict[str, float] = Field(default_factory=dict)
    weights_used: dict[str, float] = Field(default_factory=dict)
    missing_components: list[str] = Field(default_factory=list)
    score_explanation: str = ""
    contributions: list[ScoreContribution] = Field(default_factory=list)
    sensitivity: dict[str, Any] = Field(default_factory=dict)
    nearest_resource_kind: Optional[str] = None
    nearest_resource_miles: Optional[float] = None
    nearest_resource_minutes: Optional[float] = None


class ExistingResource(BaseModel):
    """Matches tools/existing_resources.py's get_existing_resources() /
    get_rural_existing_resources() row shape exactly."""

    kind: str
    name: str
    lat: float
    lon: float


class EvidenceRequest(BaseModel):
    tract: RankedTract


class EvidenceResponse(BaseModel):
    brief: str


class RoutePoint(BaseModel):
    lat: float
    lon: float


class RouteCandidate(RoutePoint):
    stop_id: str = Field(min_length=1)
    demand: float = Field(ge=0)
    need_score: float = Field(ge=0, le=100)
    population: Optional[int] = Field(default=None, ge=0)
    tract_fips: Optional[str] = None
    currently_served: bool = False
    required: bool = False


class RouteOptimizationRequest(BaseModel):
    candidates: list[RouteCandidate] = Field(min_length=1, max_length=15)
    depot: RoutePoint
    max_route_minutes: float = Field(gt=0)
    vehicle_capacity: float = Field(gt=0)
    max_stops: int = Field(gt=0, le=15)
    service_minutes: float = Field(default=20, ge=0)
    travel_time_matrix: Optional[list[list[float]]] = None
    average_speed_mph: float = Field(default=35, gt=0)
    travel_time_provider: Literal["openrouteservice", "estimate"] = "openrouteservice"


class RouteOptimizationResponse(BaseModel):
    status: Literal["optimal", "infeasible"]
    reason: Optional[str] = None
    objective: Optional[str] = None
    travel_time_source: str
    route_minutes: Optional[float] = None
    travel_minutes: Optional[float] = None
    service_minutes: Optional[float] = None
    capacity_used: Optional[float] = None
    capacity_remaining: Optional[float] = None
    priority_benefit: Optional[float] = None
    selected_stops: list[dict[str, Any]]
    unselected_stops: list[Any]
    coverage_change: Optional[dict[str, list[str]]] = None
    constraints: Optional[dict[str, float]] = None


class RouteDirectionsRequest(BaseModel):
    origin: RoutePoint
    waypoints: list[RoutePoint] = Field(default_factory=list, max_length=14)
    destination: RoutePoint
    alternatives: int = Field(default=2, ge=0, le=2)


class RouteStep(BaseModel):
    instruction: str
    distanceMiles: Optional[float] = None
    durationMinutes: Optional[float] = None


class RouteLeg(BaseModel):
    index: int
    distanceMiles: Optional[float] = None
    durationMinutes: Optional[float] = None
    steps: list[RouteStep] = Field(default_factory=list)


class RoadRoute(BaseModel):
    coordinates: list[list[float]]
    legs: list[RouteLeg] = Field(default_factory=list)
    distanceMiles: Optional[float] = None
    durationMinutes: Optional[float] = None
    alternatives: list["RoadRoute"] = Field(default_factory=list)


class VerifyRequest(BaseModel):
    tract_fips: str
    recommendation_type: str
    verification: VerificationChoice
    note: Optional[str] = ""


class EvidenceReviewRequest(BaseModel):
    source_scope: str = Field(min_length=3)
    record_key: str = Field(min_length=3)
    action: EvidenceReviewAction
    note: Optional[str] = Field(default="", max_length=2000)
