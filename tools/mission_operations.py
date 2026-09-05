"""Deterministic planning engine for Mobile Market Missions.

The language-model agent may explain these results, but it is deliberately not
the authority for inventory arithmetic, vehicle eligibility, cold-chain rules,
permit validity, approval, dispatch, or reconciliation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from threading import RLock
from typing import Any, Callable, Literal
from uuid import uuid4


MissionStatus = Literal[
    "draft", "planning", "blocked", "ready_for_approval", "approved",
    "inventory_reserved", "dispatched", "in_service", "completed",
    "reconciled", "cancelled",
]
InventoryStatus = Literal["yes", "partial", "no", "unknown"]


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | date) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: str
    unit_weight_lb: float
    unit_volume_ft3: float
    temperature_zone: Literal["ambient", "refrigerated", "frozen"]


@dataclass
class InventoryLot:
    lot_id: str
    warehouse_id: str
    sku: str
    on_hand: float
    reserved: float = 0
    allocated: float = 0
    quarantined: float = 0
    expires_on: date | None = None
    status: Literal["available", "quarantined", "unavailable"] = "available"
    updated_at: datetime = field(default_factory=_now)
    source: str = "Illustrative warehouse inventory"

    def available_to_promise(self, service_date: date) -> float:
        if self.status != "available" or (self.expires_on and self.expires_on <= service_date):
            return 0
        return max(0, self.on_hand - self.reserved - self.allocated - self.quarantined)


@dataclass(frozen=True)
class Vehicle:
    vehicle_id: str
    name: str
    payload_capacity_lb: float
    cargo_volume_ft3: float
    temperature_zones: frozenset[str]
    active: bool = True
    available: bool = True
    maintenance_current: bool = True
    inspection_valid: bool = True
    driver_id: str | None = None
    driver_available: bool = True
    depot_name: str = "Chicago Community Food Hub"


PRODUCTS = {
    "PRODUCE-BOX": Product("PRODUCE-BOX", "Fresh produce box", "produce", 12, 0.75, "ambient"),
    "MILK-COLD": Product("MILK-COLD", "Refrigerated milk case", "dairy", 36, 1.20, "refrigerated"),
    "MILK-UHT": Product("MILK-UHT", "Shelf-stable milk case", "dairy", 28, 1.00, "ambient"),
    "PROTEIN-FROZEN": Product("PROTEIN-FROZEN", "Frozen protein box", "protein", 18, 0.80, "frozen"),
    "PANTRY-BOX": Product("PANTRY-BOX", "Shelf-stable grocery box", "pantry", 16, 1.00, "ambient"),
}


def default_manifest(expected_households: int) -> list[dict[str, Any]]:
    """Calculate quantities with fixed, reviewable allocation rules."""
    rules = {
        "PRODUCE-BOX": 1,
        "MILK-COLD": 0.25,
        "PROTEIN-FROZEN": 0.5,
        "PANTRY-BOX": 1,
    }
    return [
        {
            "sku": sku,
            "product_name": PRODUCTS[sku].name,
            "required_quantity": int(expected_households * per_household + 0.9999),
            "allocation_per_household": per_household,
            "temperature_zone": PRODUCTS[sku].temperature_zone,
        }
        for sku, per_household in rules.items()
    ]


def _seed_lots(now: datetime) -> list[InventoryLot]:
    expiry = now.date() + timedelta(days=30)
    return [
        InventoryLot("LOT-PRODUCE-01", "WH-CHI-01", "PRODUCE-BOX", 140, expires_on=expiry, updated_at=now),
        InventoryLot("LOT-MILK-01", "WH-CHI-01", "MILK-COLD", 18, expires_on=now.date() + timedelta(days=20), updated_at=now),
        InventoryLot("LOT-UHT-01", "WH-CHI-01", "MILK-UHT", 48, expires_on=expiry, updated_at=now),
        InventoryLot("LOT-PROTEIN-01", "WH-CHI-01", "PROTEIN-FROZEN", 75, expires_on=expiry, updated_at=now),
        InventoryLot("LOT-PANTRY-01", "WH-CHI-01", "PANTRY-BOX", 125, expires_on=expiry, updated_at=now),
    ]


VEHICLES = [
    Vehicle(
        "VEH-REF-01", "Refrigerated Mobile Market 1", 6000, 310,
        frozenset({"ambient", "refrigerated", "frozen"}), driver_id="DRV-014",
    ),
    Vehicle(
        "VEH-CARGO-02", "Community Cargo Van 2", 2800, 180,
        frozenset({"ambient"}), driver_id="DRV-009",
    ),
]


SITE_PARTNERS = {
    "SITE-ST-JUDE": {
        "site_id": "SITE-ST-JUDE",
        "name": "St. Jude Community Center",
        "address": "Chicago, IL",
        "verified": True,
        "vehicle_access": True,
        "permit_jurisdiction": "Chicago",
        "permit": {
            "status": "verified",
            "permit_id": "DEMO-CHI-MM-2026",
            "valid_through": "2026-12-31",
            "last_verified_at": "2026-09-04T12:00:00Z",
        },
    }
}

DEFAULT_INVESTIGATIONS = {
    "INV-001": {"investigation_id": "INV-001", "status": "approved"},
}

PLANNABLE_STATUSES = {"draft", "planning", "blocked", "ready_for_approval"}
ACTIVE_ASSIGNMENT_STATUSES = {"dispatched", "in_service", "completed"}


class MissionConflictError(ValueError):
    pass


class MissionNotFoundError(KeyError):
    pass


class MissionOperationsService:
    """Thread-safe MVP repository and deterministic mission coordinator."""

    def __init__(
        self,
        *,
        now_fn=_now,
        inventory_max_age_hours: int = 24,
        investigation_lookup: Callable[[str], dict[str, Any] | None] | None = None,
    ):
        self._now_fn = now_fn
        self.inventory_max_age = timedelta(hours=inventory_max_age_hours)
        self._lots = _seed_lots(now_fn())
        self._missions: dict[str, dict[str, Any]] = {}
        self._lock = RLock()
        self._investigation_lookup = investigation_lookup or (
            lambda investigation_id: deepcopy(DEFAULT_INVESTIGATIONS.get(investigation_id))
        )

    def _event(self, mission: dict[str, Any], action: str, actor: str, detail: str) -> None:
        mission["timeline"].append({
            "event_id": f"EVT-{uuid4().hex[:10].upper()}",
            "action": action,
            "actor": actor,
            "detail": detail,
            "occurred_at": _iso(self._now_fn()),
        })

    def create_mission(self, payload: dict[str, Any], actor: str = "mission-operations-agent") -> dict[str, Any]:
        investigation_id = str(payload["investigation_id"])
        investigation = self._investigation_lookup(investigation_id)
        if not investigation or investigation.get("status") != "approved":
            raise ValueError("A mission requires an approved investigation")
        households = int(payload["expected_households"])
        if households < 1:
            raise ValueError("expected_households must be greater than zero")
        with self._lock:
            duplicate = next((m for m in self._missions.values() if
                              m["investigation_id"] == investigation_id and
                              m["service_date"] == payload["service_date"] and
                              m["status"] != "cancelled"), None)
            if duplicate:
                return deepcopy(duplicate)
            mission_id = f"MM-{len(self._missions) + 104}"
            mission = {
                "mission_id": mission_id,
                "investigation_id": investigation_id,
                "tract_fips": payload["tract_fips"],
                "community": payload["community"],
                "study_area": payload.get("study_area", "chicago"),
                "service_date": payload["service_date"],
                "service_window": payload.get("service_window", "10:00-13:00"),
                "expected_households": households,
                "warehouse_id": payload.get("warehouse_id", "WH-CHI-01"),
                "site_id": payload.get("site_id", "SITE-ST-JUDE"),
                "status": "draft",
                "version": 1,
                "manifest": default_manifest(households),
                "inventory": None,
                "vehicle": None,
                "site": None,
                "route_handoff": None,
                "approval": None,
                "reservations": [],
                "dispatch": None,
                "outcome": None,
                "blockers": [],
                "timeline": [],
                "created_at": _iso(self._now_fn()),
                "updated_at": _iso(self._now_fn()),
            }
            self._event(mission, "mission_created", actor, "Draft mission created from approved investigation")
            self._missions[mission_id] = mission
            return deepcopy(mission)

    def list_missions(self, status: str | None = None) -> list[dict[str, Any]]:
        values = self._missions.values()
        if status:
            values = (mission for mission in values if mission["status"] == status)
        return deepcopy(sorted(values, key=lambda mission: mission["created_at"], reverse=True))

    def get_mission(self, mission_id: str) -> dict[str, Any]:
        try:
            return deepcopy(self._missions[mission_id])
        except KeyError as exc:
            raise MissionNotFoundError(mission_id) from exc

    def inventory_availability(
        self,
        *,
        warehouse_id: str,
        service_date: str,
        manifest: list[dict[str, Any]],
        approved_substitutions: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        now = self._now_fn()
        selected_date = date.fromisoformat(service_date)
        approved_substitutions = approved_substitutions or {}
        warehouse_lots = [lot for lot in self._lots if lot.warehouse_id == warehouse_id]
        if not warehouse_lots:
            return {
                "status": "unknown", "warehouse_id": warehouse_id, "coverage_percent": 0,
                "checked_at": _iso(now), "last_updated_at": None,
                "reason": "No inventory source is configured for this warehouse", "items": [],
            }
        newest = max(lot.updated_at for lot in warehouse_lots)
        if now - newest > self.inventory_max_age:
            return {
                "status": "unknown", "warehouse_id": warehouse_id, "coverage_percent": 0,
                "checked_at": _iso(now), "last_updated_at": _iso(newest),
                "reason": "Inventory data is stale", "items": [],
            }

        items: list[dict[str, Any]] = []
        total_required = 0.0
        total_fulfilled = 0.0
        for line in manifest:
            sku = line["sku"]
            required = float(line["required_quantity"])
            available = sum(lot.available_to_promise(selected_date) for lot in warehouse_lots if lot.sku == sku)
            fulfilled = min(required, available)
            shortage = max(0, required - fulfilled)
            substitute = approved_substitutions.get(sku)
            substitute_quantity = 0.0
            if shortage and substitute in PRODUCTS:
                if PRODUCTS[substitute].category != PRODUCTS[sku].category:
                    raise ValueError(f"Substitute {substitute} is not in the same product category as {sku}")
                substitute_available = sum(
                    lot.available_to_promise(selected_date) for lot in warehouse_lots if lot.sku == substitute
                )
                substitute_quantity = min(shortage, substitute_available)
                fulfilled += substitute_quantity
                shortage -= substitute_quantity
            total_required += required
            total_fulfilled += fulfilled
            source_lots = [lot for lot in warehouse_lots if lot.sku in {sku, substitute}]
            items.append({
                "sku": sku,
                "product_name": PRODUCTS[sku].name,
                "required_quantity": required,
                "available_to_promise": available,
                "fulfilled_quantity": fulfilled,
                "shortage_quantity": shortage,
                "substitution": ({"sku": substitute, "quantity": substitute_quantity}
                                 if substitute_quantity else None),
                "temperature_zone": PRODUCTS[sku].temperature_zone,
                "lots": [{
                    "lot_id": lot.lot_id,
                    "available_to_promise": lot.available_to_promise(selected_date),
                    "expires_on": lot.expires_on.isoformat() if lot.expires_on else None,
                    "source": lot.source,
                    "last_updated_at": _iso(lot.updated_at),
                } for lot in source_lots],
            })
        shortages = [item for item in items if item["shortage_quantity"] > 0]
        status: InventoryStatus = "yes" if not shortages else ("partial" if total_fulfilled else "no")
        return {
            "status": status,
            "warehouse_id": warehouse_id,
            "coverage_percent": round((total_fulfilled / total_required * 100) if total_required else 0, 1),
            "checked_at": _iso(now),
            "last_updated_at": _iso(newest),
            "source": "Illustrative warehouse inventory",
            "items": items,
            "shortages": shortages,
        }

    @staticmethod
    def _load_requirements(manifest: list[dict[str, Any]], inventory: dict[str, Any]) -> dict[str, Any]:
        selected_skus: list[tuple[str, float]] = []
        for line, item in zip(manifest, inventory.get("items", []), strict=False):
            original_quantity = item.get("fulfilled_quantity", line["required_quantity"])
            substitute = item.get("substitution")
            substitute_quantity = float(substitute["quantity"]) if substitute else 0
            selected_skus.append((line["sku"], max(0, original_quantity - substitute_quantity)))
            if substitute:
                selected_skus.append((substitute["sku"], substitute_quantity))
        weight = sum(PRODUCTS[sku].unit_weight_lb * quantity for sku, quantity in selected_skus)
        volume = sum(PRODUCTS[sku].unit_volume_ft3 * quantity for sku, quantity in selected_skus)
        zones = sorted({PRODUCTS[sku].temperature_zone for sku, quantity in selected_skus if quantity > 0})
        return {"payload_weight_lb": round(weight, 1), "cargo_volume_ft3": round(volume, 1), "temperature_zones": zones}

    @staticmethod
    def _windows_overlap(left: str, right: str) -> bool:
        left_start, left_end = left.split("-", 1)
        right_start, right_end = right.split("-", 1)
        return left_start < right_end and right_start < left_end

    def _assignment_conflicts(
        self, vehicle: Vehicle, *, service_date: str, service_window: str, exclude_mission_id: str
    ) -> list[str]:
        reasons: list[str] = []
        for mission in self._missions.values():
            assigned = mission.get("vehicle") or {}
            if (
                mission["mission_id"] == exclude_mission_id
                or mission["status"] not in ACTIVE_ASSIGNMENT_STATUSES
                or mission["service_date"] != service_date
                or not self._windows_overlap(mission["service_window"], service_window)
            ):
                continue
            if assigned.get("vehicle_id") == vehicle.vehicle_id:
                reasons.append("vehicle already assigned for service window")
            if vehicle.driver_id and assigned.get("driver_id") == vehicle.driver_id:
                reasons.append("driver already assigned for service window")
        return reasons

    def eligible_vehicles(
        self,
        requirements: dict[str, Any],
        *,
        service_date: str | None = None,
        service_window: str | None = None,
        exclude_mission_id: str = "",
    ) -> dict[str, Any]:
        results = []
        required_zones = set(requirements["temperature_zones"])
        for vehicle in VEHICLES:
            reasons = []
            if not vehicle.active: reasons.append("vehicle inactive")
            if not vehicle.available: reasons.append("vehicle unavailable")
            if not vehicle.maintenance_current: reasons.append("maintenance not current")
            if not vehicle.inspection_valid: reasons.append("inspection not valid")
            if not vehicle.driver_id or not vehicle.driver_available: reasons.append("qualified driver unavailable")
            if vehicle.payload_capacity_lb < requirements["payload_weight_lb"]: reasons.append("payload capacity exceeded")
            if vehicle.cargo_volume_ft3 < requirements["cargo_volume_ft3"]: reasons.append("cargo volume exceeded")
            if not required_zones.issubset(vehicle.temperature_zones): reasons.append("temperature zones incompatible")
            if service_date and service_window:
                reasons.extend(self._assignment_conflicts(
                    vehicle,
                    service_date=service_date,
                    service_window=service_window,
                    exclude_mission_id=exclude_mission_id,
                ))
            results.append({
                "vehicle_id": vehicle.vehicle_id,
                "name": vehicle.name,
                "eligible": not reasons,
                "reasons": reasons,
                "driver_id": vehicle.driver_id,
                "payload_capacity_lb": vehicle.payload_capacity_lb,
                "cargo_volume_ft3": vehicle.cargo_volume_ft3,
                "temperature_zones": sorted(vehicle.temperature_zones),
            })
        return {"requirements": requirements, "vehicles": results}

    def plan_mission(
        self,
        mission_id: str,
        *,
        approved_substitutions: dict[str, str] | None = None,
        actor: str = "mission-operations-agent",
    ) -> dict[str, Any]:
        with self._lock:
            mission = self._missions.get(mission_id)
            if not mission:
                raise MissionNotFoundError(mission_id)
            if mission["status"] not in PLANNABLE_STATUSES:
                raise ValueError(f"Mission in {mission['status']} state cannot be replanned")
            inventory = self.inventory_availability(
                warehouse_id=mission["warehouse_id"], service_date=mission["service_date"],
                manifest=mission["manifest"], approved_substitutions=approved_substitutions,
            )
            requirements = self._load_requirements(mission["manifest"], inventory)
            fleet = self.eligible_vehicles(
                requirements,
                service_date=mission["service_date"],
                service_window=mission["service_window"],
                exclude_mission_id=mission_id,
            )
            eligible = next((row for row in fleet["vehicles"] if row["eligible"]), None)
            site = deepcopy(SITE_PARTNERS.get(mission["site_id"]))
            blockers = []
            if inventory["status"] != "yes": blockers.append("inventory_not_fulfilled")
            if not eligible: blockers.append("no_eligible_vehicle")
            if not site or not site["verified"]: blockers.append("site_not_verified")
            permit = site.get("permit") if site else None
            if not permit or permit.get("status") != "verified":
                blockers.append("permit_not_verified")
            elif date.fromisoformat(permit["valid_through"]) < date.fromisoformat(mission["service_date"]):
                blockers.append("permit_expired")
            existing_route = mission.get("route_handoff") or {}
            if existing_route.get("status") != "approved": blockers.append("route_not_approved")

            mission["inventory"] = inventory
            mission["vehicle"] = eligible
            mission["fleet_evaluation"] = fleet
            mission["site"] = site
            mission["route_handoff"] = (existing_route if existing_route.get("status") == "approved" else {
                "status": "awaiting_route_approval",
                "origin": VEHICLES[0].depot_name,
                "destination": site["name"],
                "message": "Approved site is ready for the existing Route Advisor workflow.",
            }) if site else None
            mission["blockers"] = blockers
            mission["status"] = "blocked" if blockers else "ready_for_approval"
            mission["version"] += 1
            mission["updated_at"] = _iso(self._now_fn())
            detail = ("Mission has blocking requirements: " + ", ".join(blockers)
                      if blockers else "All recorded planning checks passed; human approval is required")
            self._event(mission, "mission_planned", actor, detail)
            return deepcopy(mission)

    def record_route_approval(
        self,
        mission_id: str,
        *,
        expected_version: int,
        actor: str,
        route_id: str,
        distance_miles: float,
        duration_minutes: float,
    ) -> dict[str, Any]:
        """Record the human-approved output of the unchanged Route Advisor workflow."""
        with self._lock:
            mission = self._missions.get(mission_id)
            if not mission: raise MissionNotFoundError(mission_id)
            if mission["version"] != expected_version: raise MissionConflictError("Mission version conflict")
            if not mission.get("inventory") or mission["inventory"]["status"] != "yes":
                raise ValueError("Resolve inventory before approving the route")
            if not mission.get("site") or not mission["site"]["verified"]:
                raise ValueError("A verified community host is required before route approval")
            mission["route_handoff"] = {
                "status": "approved",
                "route_id": route_id,
                "origin": VEHICLES[0].depot_name,
                "destination": mission["site"]["name"],
                "distance_miles": distance_miles,
                "duration_minutes": duration_minutes,
                "approved_by": actor,
                "approved_at": _iso(self._now_fn()),
                "message": "Human-approved route received from the existing Route Advisor workflow.",
            }
            mission["blockers"] = [item for item in mission["blockers"] if item != "route_not_approved"]
            mission["status"] = "blocked" if mission["blockers"] else "ready_for_approval"
            mission["version"] += 1
            mission["updated_at"] = _iso(self._now_fn())
            self._event(mission, "route_approved", actor, f"Approved Route Advisor result {route_id}")
            return deepcopy(mission)

    def approve_mission(self, mission_id: str, *, expected_version: int, actor: str, note: str = "") -> dict[str, Any]:
        with self._lock:
            mission = self._missions.get(mission_id)
            if not mission: raise MissionNotFoundError(mission_id)
            if mission["version"] != expected_version: raise MissionConflictError("Mission version conflict")
            if mission["status"] != "ready_for_approval": raise ValueError("Only a dispatch-ready mission can be approved")
            mission["status"] = "approved"
            mission["approval"] = {"approved_by": actor, "approved_at": _iso(self._now_fn()), "note": note}
            mission["version"] += 1
            mission["updated_at"] = _iso(self._now_fn())
            self._event(mission, "mission_approved", actor, "Operations approval recorded; dispatch has not occurred")
            return deepcopy(mission)

    def _reserve_inventory(self, mission: dict[str, Any]) -> list[dict[str, Any]]:
        """Reserve the exact approved load while holding the service lock."""
        requested: list[tuple[str, float]] = []
        for item in mission["inventory"]["items"]:
            substitution = item.get("substitution")
            substitute_quantity = float(substitution["quantity"]) if substitution else 0
            requested.append((item["sku"], float(item["fulfilled_quantity"]) - substitute_quantity))
            if substitution:
                requested.append((substitution["sku"], substitute_quantity))

        service_date = date.fromisoformat(mission["service_date"])
        pending: list[tuple[InventoryLot, float]] = []
        for sku, quantity in requested:
            remaining = quantity
            lots = [lot for lot in self._lots if lot.warehouse_id == mission["warehouse_id"] and lot.sku == sku]
            for lot in sorted(lots, key=lambda value: value.expires_on or date.max):
                amount = min(remaining, lot.available_to_promise(service_date))
                if amount:
                    pending.append((lot, amount))
                    remaining -= amount
                if remaining <= 0:
                    break
            if remaining > 0:
                raise MissionConflictError(f"Inventory changed before dispatch; {remaining:g} units of {sku} are no longer available")

        reservations = []
        for lot, quantity in pending:
            lot.reserved += quantity
            reservations.append({
                "reservation_id": f"RSV-{uuid4().hex[:10].upper()}",
                "lot_id": lot.lot_id,
                "sku": lot.sku,
                "quantity": quantity,
                "reserved_at": _iso(self._now_fn()),
                "status": "reserved",
            })
        return reservations

    def dispatch_mission(self, mission_id: str, *, expected_version: int, actor: str) -> dict[str, Any]:
        with self._lock:
            mission = self._missions.get(mission_id)
            if not mission: raise MissionNotFoundError(mission_id)
            if mission["version"] != expected_version: raise MissionConflictError("Mission version conflict")
            if mission["status"] != "approved" or not mission.get("approval"):
                raise ValueError("Human operations approval is required before dispatch")
            if mission.get("blockers"): raise ValueError("Mission still has blocking requirements")
            selected_vehicle = next(
                (vehicle for vehicle in VEHICLES if vehicle.vehicle_id == mission["vehicle"]["vehicle_id"]),
                None,
            )
            if selected_vehicle is None:
                raise MissionConflictError("Selected vehicle no longer exists")
            assignment_conflicts = self._assignment_conflicts(
                selected_vehicle,
                service_date=mission["service_date"],
                service_window=mission["service_window"],
                exclude_mission_id=mission_id,
            )
            if assignment_conflicts:
                raise MissionConflictError("; ".join(assignment_conflicts))
            mission["reservations"] = self._reserve_inventory(mission)
            mission["status"] = "dispatched"
            mission["dispatch"] = {
                "released_by": actor,
                "released_at": _iso(self._now_fn()),
                "packet_status": "generated",
                "notification_status": "simulated_test_delivery",
            }
            mission["version"] += 1
            mission["updated_at"] = _iso(self._now_fn())
            self._event(mission, "mission_dispatched", actor, "Vehicle released; test notification logged")
            return deepcopy(mission)

    def reconcile_mission(self, mission_id: str, *, expected_version: int, actor: str, outcome: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            mission = self._missions.get(mission_id)
            if not mission: raise MissionNotFoundError(mission_id)
            if mission["version"] != expected_version: raise MissionConflictError("Mission version conflict")
            if mission["status"] not in {"dispatched", "in_service", "completed"}:
                raise ValueError("Only an executed mission can be reconciled")
            returned = {sku: float(quantity) for sku, quantity in outcome.get("inventory_returned", {}).items()}
            distributed_input = {
                sku: float(quantity) for sku, quantity in outcome.get("inventory_distributed", {}).items()
            }
            reserved_by_sku: dict[str, float] = {}
            for reservation in mission["reservations"]:
                reserved_by_sku[reservation["sku"]] = (
                    reserved_by_sku.get(reservation["sku"], 0) + float(reservation["quantity"])
                )
            unknown_skus = (set(returned) | set(distributed_input)) - set(reserved_by_sku)
            if unknown_skus:
                raise ValueError(f"Reconciliation contains unreserved SKUs: {sorted(unknown_skus)}")
            distributed: dict[str, float] = {}
            for sku, reserved_quantity in reserved_by_sku.items():
                returned_quantity = returned.get(sku, 0)
                distributed_quantity = distributed_input.get(sku, reserved_quantity - returned_quantity)
                if returned_quantity < 0 or distributed_quantity < 0:
                    raise ValueError("Reconciliation quantities cannot be negative")
                if abs(returned_quantity + distributed_quantity - reserved_quantity) > 1e-6:
                    raise ValueError(f"Distributed plus returned quantity must equal reserved quantity for {sku}")
                distributed[sku] = distributed_quantity

            remaining_returned = dict(returned)
            for reservation in mission["reservations"]:
                lot = next(lot for lot in self._lots if lot.lot_id == reservation["lot_id"])
                quantity = float(reservation["quantity"])
                returned_quantity = min(quantity, remaining_returned.get(reservation["sku"], 0))
                distributed_quantity = quantity - returned_quantity
                lot.reserved -= quantity
                lot.on_hand -= distributed_quantity
                remaining_returned[reservation["sku"]] = (
                    remaining_returned.get(reservation["sku"], 0) - returned_quantity
                )
                reservation.update({
                    "status": "reconciled",
                    "distributed_quantity": distributed_quantity,
                    "returned_quantity": returned_quantity,
                })
            mission["status"] = "reconciled"
            mission["outcome"] = {
                **outcome,
                "inventory_distributed": distributed,
                "inventory_returned": returned,
                "reconciled_by": actor,
                "reconciled_at": _iso(self._now_fn()),
            }
            mission["version"] += 1
            mission["updated_at"] = _iso(self._now_fn())
            self._event(mission, "mission_reconciled", actor, f"Recorded {outcome.get('households_served', 0)} households served")
            return deepcopy(mission)

    def summary(self) -> dict[str, Any]:
        missions = list(self._missions.values())
        return {
            "total_missions": len(missions),
            "awaiting_approval": sum(m["status"] == "ready_for_approval" for m in missions),
            "blocked": sum(m["status"] == "blocked" for m in missions),
            "dispatched": sum(m["status"] in {"dispatched", "in_service"} for m in missions),
            "awaiting_reconciliation": sum(m["status"] == "completed" for m in missions),
            "inventory_shortages": sum(bool(m.get("inventory", {}).get("shortages")) for m in missions if m.get("inventory")),
            "illustrative_data": True,
        }


mission_operations = MissionOperationsService()
