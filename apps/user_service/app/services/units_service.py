"""Units service: units, parking zones, units_count recompute, step completion."""

from __future__ import annotations

from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.projects_repository import ProjectsRepository
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.schemas.enums import ProjectSetupStep
from apps.user_service.app.schemas.project_inventory import (
    CreateParkingZoneRequest,
    CreateUnitRequest,
    UpdateUnitRequest,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.project_serialization import serialize_row
from libs.shared_utils.http_exceptions import ConflictException, NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


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
