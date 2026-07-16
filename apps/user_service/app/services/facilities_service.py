"""Facilities service: CRUD, parking slot provisioning, and step completion."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.facilities_repository import (
    FacilitiesRepository,
)
from apps.user_service.app.db.repositories.parking_slots_repository import (
    ParkingSlotsRepository,
)
from apps.user_service.app.schemas.enums import ProjectSetupStep
from apps.user_service.app.schemas.project_inventory import (
    CreateFacilityRequest,
    UpdateFacilityRequest,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.services.project_setup_validation import (
    normalize_facility_type,
    validate_facility_payload,
)
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.project_serialization import serialize_row
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


class FacilitiesService:
    """Business logic for the facilities step."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.facilities_repo = FacilitiesRepository(db_connection)
        self.parking_slots_repo = ParkingSlotsRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    def _serialize_create_facility(self, body: CreateFacilityRequest) -> dict[str, Any]:
        """Map create request to DB-ready dict."""
        data = body.model_dump()
        data["status"] = body.status.value
        data["location_type"] = body.location_type.value
        if body.parking_user_type:
            data["parking_user_type"] = body.parking_user_type.value
        data["extra_attributes"] = body.extra_attributes or {}
        return data

    def _serialize_update_facility(self, body: UpdateFacilityRequest) -> dict[str, Any]:
        """Map patch request to DB-ready dict."""
        data = body.model_dump(exclude_unset=True, exclude_none=True)
        if body.status:
            data["status"] = body.status.value
        if body.location_type:
            data["location_type"] = body.location_type.value
        if body.parking_user_type:
            data["parking_user_type"] = body.parking_user_type.value
        return data

    async def _ensure_facility(self, *, project_id: str, facility_id: str) -> dict[str, Any]:
        """Return the facility row or raise 404."""
        await self.setup_service.ensure_project(project_id=project_id)
        facility = await self.facilities_repo.get_facility(
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=facility_id,
        )
        if not facility:
            raise NotFoundException(
                message_key="project_setup.errors.facility_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return facility

    async def _provision_parking_slots(
        self,
        *,
        project_id: str,
        facility_id: str,
        slot_count: int,
    ) -> None:
        """Create numbered parking slots for a parking facility."""
        await self.parking_slots_repo.bulk_insert_slots(
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=facility_id,
            slot_count=slot_count,
        )

    async def create_facility(
        self, *, project_id: str, body: CreateFacilityRequest
    ) -> dict[str, Any]:
        """Create a facility and provision parking slots when applicable."""
        await self.setup_service.ensure_project(project_id=project_id)
        data = self._serialize_create_facility(body)
        validate_facility_payload(data)
        data["organization_id"] = self._org_id
        data["project_id"] = project_id
        inserted = await self.facilities_repo.insert_facility(data)
        if normalize_facility_type(body.facility_type) == "parking" and body.parking_slots:
            await self._provision_parking_slots(
                project_id=project_id,
                facility_id=str(inserted["id"]),
                slot_count=body.parking_slots,
            )
        return serialize_row(inserted)

    async def list_facilities(self, *, project_id: str) -> list[dict[str, Any]]:
        """List facilities for a project."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = await self.facilities_repo.list_facilities(
            organization_id=self._org_id, project_id=project_id
        )
        return [serialize_row(row) for row in rows]

    async def list_parking_slots(
        self,
        *,
        project_id: str,
        facility_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List parking slots for a parking facility."""
        await self._ensure_facility(project_id=project_id, facility_id=facility_id)
        rows = await self.parking_slots_repo.list_by_facility(
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=facility_id,
            status=status,
        )
        return [serialize_row(row) for row in rows]

    async def update_facility(
        self, *, project_id: str, facility_id: str, body: UpdateFacilityRequest
    ) -> dict[str, Any]:
        """Patch a facility."""
        current = await self._ensure_facility(project_id=project_id, facility_id=facility_id)
        patch = self._serialize_update_facility(body)
        merged = {**serialize_row(current), **patch}
        validate_facility_payload(merged)
        updated = await self.facilities_repo.update_facility(
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=facility_id,
            update_data=patch,
        )
        return serialize_row(updated or {})

    async def delete_facility(self, *, project_id: str, facility_id: str) -> dict[str, Any]:
        """Delete a facility and its parking slots."""
        current = await self._ensure_facility(project_id=project_id, facility_id=facility_id)
        await self.parking_slots_repo.delete_by_facility(
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=facility_id,
        )
        await self.facilities_repo.delete_facility(
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=facility_id,
        )
        return {"old_data": serialize_row(current), "new_data": None}

    async def complete_facilities(self, *, project_id: str) -> dict[str, Any]:
        """Mark the facilities step complete."""
        return await self.setup_service.complete_step(
            project_id=project_id,
            step_key=ProjectSetupStep.FACILITIES.value,
        )
