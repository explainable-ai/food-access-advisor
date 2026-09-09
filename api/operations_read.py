"""Read-only Mission Operations endpoints backed by DynamoDB."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import require_staff_user
from services.mission_preview import build_mission_preview
from storage.mission_memory import (
    DynamoDBMissionMemory,
    MissionMemoryConflict,
    MissionMemoryError,
    get_mission_memory,
)
from storage.operations_repository import (
    OperationsDataError,
    OperationsRepository,
    get_operations_repository,
)


router = APIRouter(prefix="/api/operations", tags=["mission-operations"])


class MissionReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    mission: dict[str, Any]
    study_area: Literal["chicago_neighborhoods", "rural_fringe"] | None = None
    request_intent: dict[str, Any] = Field(default_factory=dict)
    note: str = Field(default="", max_length=2000)


def _list(entity_type: str, repository: OperationsRepository) -> list[dict[str, Any]]:
    try:
        return repository.list_entities(entity_type)
    except OperationsDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/summary")
def operations_summary(repository: OperationsRepository = Depends(get_operations_repository)):
    try:
        return repository.summary()
    except OperationsDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/products")
def products(repository: OperationsRepository = Depends(get_operations_repository)):
    return _list("product", repository)


@router.get("/inventory-lots")
def inventory_lots(repository: OperationsRepository = Depends(get_operations_repository)):
    return _list("inventory_lot", repository)


@router.get("/vehicles")
def vehicles(repository: OperationsRepository = Depends(get_operations_repository)):
    return _list("vehicle", repository)


@router.get("/drivers")
def drivers(repository: OperationsRepository = Depends(get_operations_repository)):
    return _list("driver", repository)


@router.get("/sites")
def sites(repository: OperationsRepository = Depends(get_operations_repository)):
    return _list("site_partner", repository)


@router.get("/permit-rules")
def permit_rules(repository: OperationsRepository = Depends(get_operations_repository)):
    return _list("permit_rule", repository)


@router.get("/permits")
def permits(repository: OperationsRepository = Depends(get_operations_repository)):
    return _list("permit", repository)


@router.get("/manifest-policies")
def manifest_policies(repository: OperationsRepository = Depends(get_operations_repository)):
    return _list("manifest_policy", repository)


@router.get("/scenarios")
def scenarios(repository: OperationsRepository = Depends(get_operations_repository)):
    return _list("demo_scenario", repository)


@router.get("/scenarios/{scenario_id}")
def scenario(scenario_id: str, repository: OperationsRepository = Depends(get_operations_repository)):
    try:
        item = repository.get_entity("demo_scenario", scenario_id)
    except OperationsDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_id} was not found")
    return item


@router.get("/scenarios/{scenario_id}/preview")
def mission_preview(
    scenario_id: str,
    repository: OperationsRepository = Depends(get_operations_repository),
):
    try:
        item = repository.get_entity("demo_scenario", scenario_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Scenario {scenario_id} was not found")
        return build_mission_preview(item, repository)
    except OperationsDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/missions/{mission_id}/review")
def review_mission(
    mission_id: str,
    request: MissionReviewRequest,
    staff_user: dict[str, Any] = Depends(require_staff_user),
    memory: DynamoDBMissionMemory = Depends(get_mission_memory),
):
    """Persist a staff decision; only approvals become retrievable memory."""
    reviewed_by = str(
        staff_user.get("email")
        or staff_user.get("username")
        or staff_user.get("cognito:username")
        or staff_user.get("sub")
    )
    try:
        review = memory.record_review(
            mission_id=mission_id,
            action=request.action,
            mission=request.mission,
            reviewed_by=reviewed_by,
            note=request.note,
            study_area=request.study_area,
            request_intent=request.request_intent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MissionMemoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissionMemoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "review_recorded",
        "message": (
            "Approved mission added to operational memory."
            if request.action == "approve"
            else "Rejection recorded for audit; the draft was not added to memory."
        ),
        "memory_eligible": request.action == "approve",
        "review": review,
    }


@router.get("/mission-memory")
def approved_mission_memory(
    study_area: Literal["chicago_neighborhoods", "rural_fringe"] | None = None,
    categories: str | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=20),
    _staff_user: dict[str, Any] = Depends(require_staff_user),
    memory: DynamoDBMissionMemory = Depends(get_mission_memory),
):
    """Return compact approved mission summaries, excluding rejected drafts."""
    requested_categories = [
        value.strip().lower() for value in (categories or "").split(",") if value.strip()
    ]
    try:
        return memory.list_approved(
            study_area=study_area,
            categories=requested_categories,
            limit=limit,
        )
    except MissionMemoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
