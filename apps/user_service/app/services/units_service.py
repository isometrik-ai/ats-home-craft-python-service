"""Units service: units, parking zones, units_count recompute, step completion."""

from __future__ import annotations

from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.maintenance_fee_invoices_repository import (
    MaintenanceFeeInvoicesRepository,
)
from apps.user_service.app.db.repositories.projects_repository import ProjectsRepository
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.schemas.enums import ProjectSetupStep
from apps.user_service.app.schemas.project_inventory import (
    CreateParkingZoneRequest,
    CreateUnitRequest,
    UpdateUnitRequest,
)
from apps.user_service.app.services.fee_calculation_service import (
    convert_minor_to_major,
)
from apps.user_service.app.services.inventory_service import is_sold_status
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.project_serialization import serialize_row
from libs.shared_utils.http_exceptions import ConflictException, NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


def format_contact_display_name(
    *,
    prefix: str | None,
    first_name: str | None,
    last_name: str | None,
) -> str:
    """Build a display name from contact name parts."""
    return " ".join(
        part
        for part in [
            (prefix or "").strip(),
            (first_name or "").strip(),
            (last_name or "").strip(),
        ]
        if part
    ).strip()


def resolve_occupancy_label(status: str) -> str:
    """Map raw unit status to inventory occupancy label."""
    if is_sold_status(status):
        return "sold"
    if status == "under_maintenance":
        return "under_maintenance"
    return "unsold"


def resolve_unit_facing(row: dict[str, Any]) -> str | None:
    """Pick facing from config fields based on config kind."""
    config_kind = row.get("config_kind")
    if config_kind == "plot":
        facing = row.get("config_facing")
    else:
        facing = row.get("default_facing") or row.get("config_facing")
    return str(facing) if facing is not None else None


def resolve_carpet_area_sqft(row: dict[str, Any]) -> float | None:
    """Pick display area from config fields."""
    if row.get("carpet_area_sqft") is not None:
        return float(row["carpet_area_sqft"])
    if row.get("area_sqft") is not None:
        return float(row["area_sqft"])
    if row.get("plot_size_sqft") is not None:
        return float(row["plot_size_sqft"])
    return None


def build_location_label(
    *,
    tower_name: str | None,
    floor_display_name: str | None,
    floor_level_number: int | None,
) -> str | None:
    """Build a label like 'Tower A · Floor 18'."""
    tower_part = (tower_name or "").strip()
    floor_part = (floor_display_name or "").strip()
    if not floor_part and floor_level_number is not None:
        floor_part = f"Floor {floor_level_number}"
    if tower_part and floor_part:
        return f"{tower_part} · {floor_part}"
    return tower_part or floor_part or None


def pick_unit_owner(residents: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the owner row from active unit residents."""
    if not residents:
        return None
    for row in residents:
        if row.get("is_primary"):
            return row
    for row in residents:
        if row.get("contact_type") == "Owner":
            return row
    for row in residents:
        if row.get("contact_type") != "Family":
            return row
    return residents[0]


class UnitsService:
    """Business logic for the floor plans / units step."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.units_repo = UnitsRepository(db_connection)
        self.projects_repo = ProjectsRepository(db_connection)
        self.invoices_repo = MaintenanceFeeInvoicesRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    async def _recount(self, *, project_id: str) -> None:
        """Recompute the project's units_count."""
        await self.projects_repo.recompute_units_count(
            organization_id=self._org_id, project_id=project_id
        )

    async def _ensure_unit(self, *, project_id: str, unit_id: str) -> dict[str, Any]:
        """Return the unit row or raise 404."""
        await self.setup_service.ensure_project(project_id=project_id)
        unit = await self.units_repo.get_unit(
            organization_id=self._org_id, project_id=project_id, unit_id=unit_id
        )
        if not unit:
            raise NotFoundException(
                message_key="project_setup.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return unit

    async def create_unit(self, *, project_id: str, body: CreateUnitRequest) -> dict[str, Any]:
        """Create a unit and recompute units_count."""
        await self.setup_service.ensure_project(project_id=project_id)
        data = body.model_dump()
        data["status"] = body.status.value
        data["organization_id"] = self._org_id
        data["project_id"] = project_id
        try:
            inserted = await self.units_repo.insert_unit(data)
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_setup.errors.duplicate_code",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        await self._recount(project_id=project_id)
        return serialize_row(inserted)

    async def list_units(self, *, project_id: str) -> list[dict[str, Any]]:
        """List units for a project."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = await self.units_repo.list_units(organization_id=self._org_id, project_id=project_id)
        return [serialize_row(row) for row in rows]

    async def get_unit_detail(self, *, project_id: str, unit_id: str) -> dict[str, Any]:
        """Return full unit detail for inventory slide-out and registry screens."""
        await self.setup_service.ensure_project(project_id=project_id)
        row = await self.units_repo.get_unit_detail_base(
            organization_id=self._org_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        if not row:
            raise NotFoundException(
                message_key="project_setup.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        residents_raw = await self.units_repo.list_unit_residents(
            organization_id=self._org_id,
            unit_id=unit_id,
        )
        vehicles_count, parking_slots_assigned = await self.units_repo.count_unit_vehicles(
            organization_id=self._org_id,
            unit_id=unit_id,
        )

        residents = [
            {
                "contact_id": str(resident["contact_id"]),
                "contact_unit_id": str(resident["contact_unit_id"]),
                "display_name": format_contact_display_name(
                    prefix=resident.get("prefix"),
                    first_name=resident.get("first_name"),
                    last_name=resident.get("last_name"),
                ),
                "contact_type": resident.get("contact_type") or "",
                "relationship": resident.get("relationship") or "",
                "is_primary": bool(resident.get("is_primary")),
            }
            for resident in residents_raw
        ]
        owner_row = pick_unit_owner(residents_raw)
        owner = None
        if owner_row:
            owner = {
                "contact_id": str(owner_row["contact_id"]),
                "contact_unit_id": str(owner_row["contact_unit_id"]),
                "display_name": format_contact_display_name(
                    prefix=owner_row.get("prefix"),
                    first_name=owner_row.get("first_name"),
                    last_name=owner_row.get("last_name"),
                ),
                "contact_type": owner_row.get("contact_type") or "",
                "relationship": owner_row.get("relationship") or "",
                "is_primary": bool(owner_row.get("is_primary")),
            }

        status = str(row.get("status") or "")
        tower = None
        if row.get("tower_id"):
            tower = {
                "id": str(row["tower_id"]),
                "name": row.get("tower_name") or "",
                "code": row.get("tower_code") or "",
                "tower_type": row.get("tower_type") or "",
            }
        floor = None
        if row.get("floor_id"):
            floor = {
                "id": str(row["floor_id"]),
                "display_name": row.get("floor_display_name") or "",
                "level_number": int(row.get("floor_level_number") or 0),
            }
        config = None
        if row.get("config_id"):
            config = {
                "id": str(row["config_id"]),
                "config_kind": str(row.get("config_kind") or ""),
                "name": row.get("config_name") or "",
                "code": row.get("config_code") or "",
                "display_label": row.get("config_display_label"),
                "bedrooms": float(row["bedrooms"]) if row.get("bedrooms") is not None else None,
                "bathrooms": float(row["bathrooms"]) if row.get("bathrooms") is not None else None,
                "area_sqft": float(row["area_sqft"]) if row.get("area_sqft") is not None else None,
                "carpet_area_sqft": (
                    float(row["carpet_area_sqft"])
                    if row.get("carpet_area_sqft") is not None
                    else None
                ),
                "parking_entitlement": int(row.get("parking_entitlement") or 0),
                "default_facing": (
                    str(row["default_facing"]) if row.get("default_facing") is not None else None
                ),
                "facing": str(row["config_facing"])
                if row.get("config_facing") is not None
                else None,
                "commercial_unit_type": row.get("commercial_unit_type"),
            }
        plot_item = None
        if row.get("plot_item_id"):
            plot_item = {
                "id": str(row["plot_item_id"]),
                "plot_no": row.get("plot_no") or "",
                "size_sqft": float(row.get("plot_size_sqft") or 0),
                "status": str(row.get("plot_item_status") or ""),
                "description": row.get("plot_description"),
            }

        parking_entitlement = int(row.get("parking_entitlement") or 0)
        unit_id = str(row["id"])
        outstanding_minor = await self.invoices_repo.sum_outstanding_by_unit(
            organization_id=self._org_id,
            unit_id=unit_id,
        )
        latest_fee_minor = await self.invoices_repo.latest_monthly_fee_by_unit(
            organization_id=self._org_id,
            unit_id=unit_id,
        )
        return {
            "id": unit_id,
            "project_id": str(row["project_id"]),
            "code": row.get("code") or "",
            "unit_label": row.get("unit_label"),
            "status": status,
            "occupancy_label": resolve_occupancy_label(status),
            "is_sold": is_sold_status(status),
            "is_parking": bool(row.get("is_parking")),
            "sort_order": int(row.get("sort_order") or 0),
            "location_label": build_location_label(
                tower_name=row.get("tower_name"),
                floor_display_name=row.get("floor_display_name"),
                floor_level_number=row.get("floor_level_number"),
            ),
            "carpet_area_sqft": resolve_carpet_area_sqft(row),
            "facing": resolve_unit_facing(row),
            "parking_entitlement": parking_entitlement,
            "parking_slots_assigned": parking_slots_assigned,
            "tower": tower,
            "floor": floor,
            "config": config,
            "plot_item": plot_item,
            "owner": owner,
            "residents": residents,
            "vehicles_count": vehicles_count,
            "financials": {
                "base_fee_monthly": (
                    convert_minor_to_major(latest_fee_minor)
                    if latest_fee_minor is not None
                    else None
                ),
                "outstanding_amount": (
                    convert_minor_to_major(outstanding_minor)
                    if outstanding_minor > 0
                    else (0.0 if latest_fee_minor is not None else None)
                ),
                "currency": "INR",
            },
            "created_at": serialize_row(row)["created_at"],
            "updated_at": serialize_row(row)["updated_at"],
        }

    async def update_unit(
        self, *, project_id: str, unit_id: str, body: UpdateUnitRequest
    ) -> dict[str, Any]:
        """Patch a unit and recompute units_count."""
        await self._ensure_unit(project_id=project_id, unit_id=unit_id)
        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        if "status" in patch and body.status:
            patch["status"] = body.status.value
        try:
            updated = await self.units_repo.update_unit(
                organization_id=self._org_id,
                project_id=project_id,
                unit_id=unit_id,
                update_data=patch,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_setup.errors.duplicate_code",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        await self._recount(project_id=project_id)
        return serialize_row(updated or {})

    async def delete_unit(self, *, project_id: str, unit_id: str) -> dict[str, Any]:
        """Delete a unit and recompute units_count."""
        current = await self._ensure_unit(project_id=project_id, unit_id=unit_id)
        await self.units_repo.delete_unit(
            organization_id=self._org_id, project_id=project_id, unit_id=unit_id
        )
        await self._recount(project_id=project_id)
        return {"old_data": serialize_row(current), "new_data": None}

    # -- parking zones ------------------------------------------------------

    async def create_parking_zone(
        self, *, project_id: str, body: CreateParkingZoneRequest
    ) -> dict[str, Any]:
        """Create a parking zone."""
        await self.setup_service.ensure_project(project_id=project_id)
        data = body.model_dump()
        data["organization_id"] = self._org_id
        data["project_id"] = project_id
        inserted = await self.units_repo.insert_parking_zone(data)
        return serialize_row(inserted)

    async def list_parking_zones(self, *, project_id: str) -> list[dict[str, Any]]:
        """List parking zones for a project."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = await self.units_repo.list_parking_zones(
            organization_id=self._org_id, project_id=project_id
        )
        return [serialize_row(row) for row in rows]

    async def delete_parking_zone(self, *, project_id: str, zone_id: str) -> dict[str, Any]:
        """Delete a parking zone."""
        await self.setup_service.ensure_project(project_id=project_id)
        deleted = await self.units_repo.delete_parking_zone(
            organization_id=self._org_id, project_id=project_id, zone_id=zone_id
        )
        if not deleted:
            raise NotFoundException(
                message_key="project_setup.errors.parking_zone_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return {"old_data": {"id": zone_id}, "new_data": None}

    async def complete_floor_plans(self, *, project_id: str) -> dict[str, Any]:
        """Mark the floor_plans step complete."""
        return await self.setup_service.complete_step(
            project_id=project_id,
            step_key=ProjectSetupStep.FLOOR_PLANS.value,
        )
