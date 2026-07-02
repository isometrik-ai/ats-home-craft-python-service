"""Vehicle business logic for contact onboarding."""

from __future__ import annotations

from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    ContactOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.vehicles_repository import VehiclesRepository
from apps.user_service.app.schemas.contact_onboarding import (
    CreateVehicleRequest,
    UpdateVehicleRequest,
)
from apps.user_service.app.schemas.enums import ContactOnboardingStep, VehicleType
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class VehiclesService:
    """CRUD for contact vehicles."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = VehiclesRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.onboarding_repo = ContactOnboardingRepository(db_connection)

    def _normalize_vehicle(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a vehicles row to API response shape."""
        out = dict(row)
        for key in ("id", "organization_id", "project_id", "contact_id", "unit_id"):
            if out.get(key) is not None:
                out[key] = str(out[key])
        out["created_at"] = format_iso_datetime(out.get("created_at"))
        out["updated_at"] = format_iso_datetime(out.get("updated_at"))
        return out

    async def _validate_unit_for_contact(self, *, contact_id: str, unit_id: str) -> str:
        """Ensure the unit is actively assigned to the contact; return project_id."""
        org_id = self.user_context.organization_id
        assert org_id
        has_unit = await self.contact_units_repo.contact_has_active_unit(
            organization_id=org_id,
            contact_id=contact_id,
            unit_id=unit_id,
        )
        if not has_unit:
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_not_assigned",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        unit = await self.contact_units_repo.get_unit_project(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if not unit:
            raise NotFoundException(
                message_key="contact_onboarding.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return unit["project_id"]

    async def list_vehicles(self, *, contact_id: str) -> list[dict[str, Any]]:
        """List active vehicles for the contact."""
        org_id = self.user_context.organization_id
        assert org_id
        rows = await self.repo.list_by_contact(
            organization_id=org_id,
            contact_id=contact_id,
        )
        return [self._normalize_vehicle(row) for row in rows]

    async def create_vehicle(
        self,
        *,
        contact_id: str,
        body: CreateVehicleRequest,
    ) -> dict[str, Any]:
        """Create a vehicle linked to an assigned unit."""
        org_id = self.user_context.organization_id
        assert org_id
        project_id = await self._validate_unit_for_contact(
            contact_id=contact_id,
            unit_id=body.unit_id,
        )
        try:
            row = await self.repo.create(
                organization_id=org_id,
                project_id=project_id,
                contact_id=contact_id,
                unit_id=body.unit_id,
                vehicle_type=body.vehicle_type.value,
                registration_number=body.registration_number.strip().upper(),
                make=body.make,
                model=body.model,
                color=body.color,
                photo_path=body.photo_path,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="contact_onboarding.errors.vehicle_registration_duplicate",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        return self._normalize_vehicle(row)

    async def update_vehicle(
        self,
        *,
        contact_id: str,
        vehicle_id: str,
        body: UpdateVehicleRequest,
    ) -> dict[str, Any]:
        """Patch a vehicle owned by the contact."""
        org_id = self.user_context.organization_id
        assert org_id
        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        if "vehicle_type" in patch and isinstance(patch["vehicle_type"], VehicleType):
            patch["vehicle_type"] = patch["vehicle_type"].value
        if "registration_number" in patch and patch["registration_number"]:
            patch["registration_number"] = patch["registration_number"].strip().upper()
        if "unit_id" in patch and patch["unit_id"]:
            project_id = await self._validate_unit_for_contact(
                contact_id=contact_id,
                unit_id=patch["unit_id"],
            )
            patch["project_id"] = project_id
        try:
            row = await self.repo.update(
                organization_id=org_id,
                contact_id=contact_id,
                vehicle_id=vehicle_id,
                update_data=patch,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="contact_onboarding.errors.vehicle_registration_duplicate",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._normalize_vehicle(row)

    async def remove_vehicle(self, *, contact_id: str, vehicle_id: str) -> dict[str, Any]:
        """Soft-remove a vehicle owned by the contact."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.repo.soft_remove(
            organization_id=org_id,
            contact_id=contact_id,
            vehicle_id=vehicle_id,
        )
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    async def complete_vehicles_step(self, *, contact_id: str) -> None:
        """Mark the vehicles onboarding step complete."""
        org_id = self.user_context.organization_id
        assert org_id
        await self.onboarding_repo.complete_step(
            organization_id=org_id,
            contact_id=contact_id,
            step_key=ContactOnboardingStep.VEHICLES.value,
        )
