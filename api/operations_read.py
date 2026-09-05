"""Read-only Mission Operations endpoints backed by DynamoDB."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from storage.operations_repository import (
    OperationsDataError,
    OperationsRepository,
    get_operations_repository,
)


router = APIRouter(prefix="/api/operations", tags=["mission-operations"])


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
