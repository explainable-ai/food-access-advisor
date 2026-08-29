"""Pydantic request/response models for the FastAPI backend.

Field names deliberately mirror the existing tool return shapes verbatim
(tools/flagged_tracts.py's row, tools/impact_metrics.py's per-region dict)
rather than inventing a new API-specific shape -- one less thing to keep in
sync as those tools evolve.
"""

from typing import Optional

from pydantic import BaseModel


class AdvisorRequest(BaseModel):
    question: str


class AdvisorResponse(BaseModel):
    answer: str


class FlaggedTract(BaseModel):
    id: Optional[int] = None
    tract_fips: str
    recommendation_type: str
    source_agent: str
    population: Optional[int] = None
    centroid_lat: Optional[float] = None
    centroid_lon: Optional[float] = None
    flagged_date: str
    status: str
    last_checked_date: Optional[str] = None
    note: Optional[str] = None


class RegionMetrics(BaseModel):
    total_flagged: int
    unclosed: int
    resolved: int
    median_days_to_resolution: Optional[float] = None
    tracts: list[FlaggedTract]


class ImpactMetrics(BaseModel):
    urban: RegionMetrics
    rural: RegionMetrics
