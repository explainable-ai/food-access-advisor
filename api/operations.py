"""FastAPI routes for the human-approved Mobile Market Mission workflow."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import require_staff_user
from tools.mission_operations import (
    MissionConflictError,
    MissionNotFoundError,
    mission_operations,
)

router = APIRouter(prefix="/api", tags=["mission-operations"])


class MissionCreateRequest(BaseModel):
    investigation_id: str = Field(min_length=3)
    investigation_status: str
    tract_fips: str = Field(min_length=11, max_length=11)
    community: str = Field(min_length=2)
    study_area: str = "chicago"
    service_date: str
    service_window: str = "10:00-13:00"
    expected_households: int = Field(gt=0, le=5000)
    warehouse_id: str = "WH-CHI-01"
    site_id: str = "SITE-ST-JUDE"


class MissionPlanRequest(BaseModel):
    approved_substitutions: dict[str, str] = Field(default_factory=dict)


class MissionActionRequest(BaseModel):
    expected_version: int = Field(gt=0)
    note: str = Field(default="", max_length=2000)


class RouteApprovalRequest(BaseModel):
    expected_version: int = Field(gt=0)
    route_id: str = Field(min_length=3)
    distance_miles: float = Field(gt=0)
    duration_minutes: float = Field(gt=0)


class MissionReconcileRequest(MissionActionRequest):
    households_served: int = Field(ge=0)
    inventory_distributed: dict[str, float] = Field(default_factory=dict)
    inventory_returned: dict[str, float] = Field(default_factory=dict)
    temperature_exception: bool = False


def _actor(staff_user: dict[str, Any]) -> str:
    return str(staff_user.get("email") or staff_user.get("username") or
               staff_user.get("cognito:username") or staff_user.get("sub") or "staff-user")


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except MissionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Mission {exc.args[0]} was not found") from exc
    except MissionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/operations/summary")
def operations_summary():
    return mission_operations.summary()


@router.get("/missions")
def list_missions(status: str | None = None):
    return mission_operations.list_missions(status=status)


@router.post("/missions", status_code=201)
def create_mission(request: MissionCreateRequest):
    return _call(mission_operations.create_mission, request.model_dump())


@router.get("/missions/{mission_id}")
def get_mission(mission_id: str):
    return _call(mission_operations.get_mission, mission_id)


@router.post("/missions/{mission_id}/plan")
def plan_mission(mission_id: str, request: MissionPlanRequest):
    return _call(
        mission_operations.plan_mission,
        mission_id,
        approved_substitutions=request.approved_substitutions,
    )


@router.post("/missions/{mission_id}/approve")
def approve_mission(
    mission_id: str,
    request: MissionActionRequest,
    staff_user: dict[str, Any] = Depends(require_staff_user),
):
    return _call(
        mission_operations.approve_mission,
        mission_id,
        expected_version=request.expected_version,
        actor=_actor(staff_user),
        note=request.note,
    )


@router.post("/missions/{mission_id}/route-approval")
def record_route_approval(
    mission_id: str,
    request: RouteApprovalRequest,
    staff_user: dict[str, Any] = Depends(require_staff_user),
):
    return _call(
        mission_operations.record_route_approval,
        mission_id,
        expected_version=request.expected_version,
        actor=_actor(staff_user),
        route_id=request.route_id,
        distance_miles=request.distance_miles,
        duration_minutes=request.duration_minutes,
    )


@router.post("/missions/{mission_id}/dispatch")
def dispatch_mission(
    mission_id: str,
    request: MissionActionRequest,
    staff_user: dict[str, Any] = Depends(require_staff_user),
):
    return _call(
        mission_operations.dispatch_mission,
        mission_id,
        expected_version=request.expected_version,
        actor=_actor(staff_user),
    )


@router.post("/missions/{mission_id}/reconcile")
def reconcile_mission(
    mission_id: str,
    request: MissionReconcileRequest,
    staff_user: dict[str, Any] = Depends(require_staff_user),
):
    outcome = request.model_dump(exclude={"expected_version", "note"})
    if request.note:
        outcome["note"] = request.note
    return _call(
        mission_operations.reconcile_mission,
        mission_id,
        expected_version=request.expected_version,
        actor=_actor(staff_user),
        outcome=outcome,
    )


@router.get("/inventory/availability")
def inventory_availability(
    expected_households: int = Query(gt=0, le=5000),
    service_date: str = Query(),
    warehouse_id: str = "WH-CHI-01",
):
    from tools.mission_operations import default_manifest

    return _call(
        mission_operations.inventory_availability,
        warehouse_id=warehouse_id,
        service_date=service_date,
        manifest=default_manifest(expected_households),
    )


@router.get("/vehicles/eligible")
def eligible_vehicles(payload_weight_lb: float = Query(gt=0), cargo_volume_ft3: float = Query(gt=0),
                      temperature_zones: str = "ambient"):
    return mission_operations.eligible_vehicles({
        "payload_weight_lb": payload_weight_lb,
        "cargo_volume_ft3": cargo_volume_ft3,
        "temperature_zones": [value.strip() for value in temperature_zones.split(",") if value.strip()],
    })
