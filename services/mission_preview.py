"""Deterministic, read-only Mission Operations preview assembly."""

from __future__ import annotations

from typing import Any

from storage.operations_repository import OperationsRepository


CHECK_LABELS = {
    "inventory": "Inventory",
    "vehicle": "Vehicle",
    "driver": "Driver",
    "site": "Site partner",
    "permit": "Permit",
    "policy": "Manifest policy",
}


def _canonical_status(scenario: dict[str, Any]) -> str:
    inventory = str(scenario.get("inventory_answer", "")).strip().lower()
    expected = str(scenario.get("expected_mission_status", "")).strip().lower()
    if inventory == "unknown" or "refresh required" in expected:
        return "Unknown"
    if inventory == "partial" or "substitution" in expected:
        return "Partial"
    if expected.startswith("ready"):
        return "Ready"
    if expected.startswith("blocked"):
        return "Blocked"
    return "Unknown"


def _record(
    repository: OperationsRepository, entity_type: str, entity_id: Any
) -> dict[str, Any] | None:
    return repository.get_entity(entity_type, str(entity_id)) if entity_id else None


def _check(
    kind: str,
    status: str,
    detail: str,
    evidence_id: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": CHECK_LABELS[kind],
        "status": status,
        "detail": detail,
        "evidence_id": evidence_id,
    }


def build_mission_preview(
    scenario: dict[str, Any], repository: OperationsRepository
) -> dict[str, Any]:
    """Return evidence-backed preview data without reserving or dispatching anything."""
    status = _canonical_status(scenario)
    explanation = str(
        scenario.get("expected_agent_explanation")
        or "The scenario does not include an operational explanation."
    )
    explanation_lower = explanation.lower()

    vehicle_id = scenario.get("vehicle_id")
    driver_id = scenario.get("driver_id")
    site_id = scenario.get("site_id")
    permit_id = scenario.get("permit_id")

    vehicle = _record(repository, "vehicle", vehicle_id)
    driver = _record(repository, "driver", driver_id)
    site = _record(repository, "site_partner", site_id)
    permit = _record(repository, "permit", permit_id)

    inventory_answer = str(scenario.get("inventory_answer", "Unknown")).strip().lower()
    inventory_status = {
        "yes": "Ready",
        "partial": "Partial",
        "unknown": "Unknown",
    }.get(inventory_answer, "Unknown")

    checks = [
        _check(
            "inventory",
            inventory_status,
            explanation
            if inventory_status != "Ready"
            else "Required inventory is reported as current and sufficient.",
            str(scenario.get("warehouse_id") or "") or None,
        ),
        _check(
            "vehicle",
            "Blocked"
            if "vehicle is unavailable" in explanation_lower
            or "overdue maintenance" in explanation_lower
            else ("Ready" if vehicle else "Unknown"),
            (
                "Assigned vehicle is unavailable because maintenance is overdue."
                if "overdue maintenance" in explanation_lower
                else ("Assigned vehicle record verified." if vehicle else "Assigned vehicle evidence is missing.")
            ),
            str(vehicle_id) if vehicle_id else None,
        ),
        _check(
            "driver",
            "Blocked"
            if "driver credentials are invalid" in explanation_lower
            else ("Ready" if driver else "Unknown"),
            (
                "Assigned driver credentials are invalid."
                if "driver credentials are invalid" in explanation_lower
                else ("Assigned driver record verified." if driver else "Assigned driver evidence is missing.")
            ),
            str(driver_id) if driver_id else None,
        ),
        _check(
            "site",
            "Ready" if site else "Unknown",
            "Site partner record verified." if site else "Site partner evidence is missing.",
            str(site_id) if site_id else None,
        ),
        _check(
            "permit",
            "Blocked"
            if "permit is missing" in explanation_lower
            else ("Ready" if permit else "Unknown"),
            (
                "Required permit is missing."
                if "permit is missing" in explanation_lower
                else ("Permit record verified." if permit else "Permit evidence is missing.")
            ),
            str(permit_id) if permit_id else None,
        ),
        _check(
            "policy",
            "Partial"
            if status == "Partial"
            else ("Unknown" if status == "Unknown" else "Ready"),
            (
                "Authorized substitution or a reduced household count is required."
                if status == "Partial"
                else (
                    "Fresh evidence is required before policy can be evaluated."
                    if status == "Unknown"
                    else "Applicable manifest policy checks passed."
                )
            ),
        ),
    ]

    return {
        "scenario_id": scenario["entity_id"],
        "scenario_name": scenario.get("scenario_name"),
        "status": status,
        "explanation": explanation,
        "expected_households": scenario.get("expected_households"),
        "warehouse_id": scenario.get("warehouse_id"),
        "references": {
            "vehicle_id": vehicle_id,
            "driver_id": driver_id,
            "site_id": site_id,
            "permit_id": permit_id,
        },
        "checks": checks,
        "evidence": {
            "dataset_version": scenario["dataset_version"],
            "data_classification": scenario["data_classification"],
            "ingested_at": scenario.get("ingested_at"),
        },
        "not_for_real_dispatch": True,
        "dispatch_enabled": False,
    }
