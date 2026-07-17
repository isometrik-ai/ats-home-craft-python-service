"""Vehicle business logic for contact onboarding."""

from __future__ import annotations

from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    ContactOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_unit_onboarding_repository import (
    ContactUnitOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.parking_slots_repository import (
    ParkingSlotsRepository,
)
from apps.user_service.app.db.repositories.vehicles_repository import VehiclesRepository
from apps.user_service.app.schemas.contact_onboarding import (
    CreateVehicleRequest,
    ReviewVehicleRequest,
    UpdateVehicleRequest,
)
from apps.user_service.app.schemas.enums import (
    ContactOnboardingStep,
    VehicleFuelType,
    VehicleStatus,
    VehicleType,
)
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
        self.parking_slots_repo = ParkingSlotsRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.onboarding_repo = ContactOnboardingRepository(db_connection)
        self.unit_onboarding_repo = ContactUnitOnboardingRepository(db_connection)

    def _normalize_vehicle(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a vehicles row to API response shape."""
        out = dict(row)
        for key in (
            "id",
            "organization_id",
            "project_id",
            "contact_id",
            "unit_id",
            "parking_slot_id",
        ):
            if out.get(key) is not None:
                out[key] = str(out[key])
        photo_paths = out.get("photo_paths") or []
        out["photo_paths"] = list(photo_paths)
        out["created_at"] = format_iso_datetime(out.get("created_at"))
        out["updated_at"] = format_iso_datetime(out.get("updated_at"))
        out["status_updated_at"] = format_iso_datetime(out.get("status_updated_at"))
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

    async def list_vehicles(
        self,
        *,
        contact_id: str,
        unit_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List active vehicles for the contact, optionally filtered by unit."""
        org_id = self.user_context.organization_id
        assert org_id
        if unit_id:
            await self._validate_unit_for_contact(contact_id=contact_id, unit_id=unit_id)
        rows = await self.repo.list_by_contact(
            organization_id=org_id,
            contact_id=contact_id,
            unit_id=unit_id,
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
                photo_paths=body.photo_paths,
                fuel_type=body.fuel_type.value if body.fuel_type else None,
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
        if "fuel_type" in patch and isinstance(patch["fuel_type"], VehicleFuelType):
            patch["fuel_type"] = patch["fuel_type"].value
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

    async def withdraw_vehicle(self, *, contact_id: str, vehicle_id: str) -> None:
        """Hard-delete a pending vehicle request (before admin approval)."""
        org_id = self.user_context.organization_id
        assert org_id
        existing = await self.repo.get_by_id(
            organization_id=org_id,
            contact_id=contact_id,
            vehicle_id=vehicle_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        status = existing.get("status")
        if status != VehicleStatus.PENDING.value:
            raise ValidationException(
                message_key="contact_onboarding.errors.vehicle_withdraw_not_allowed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self.repo.delete(
            organization_id=org_id,
            contact_id=contact_id,
            vehicle_id=vehicle_id,
        )

    async def remove_vehicle(self, *, contact_id: str, vehicle_id: str) -> dict[str, Any]:
        """Soft-remove an approved vehicle (status removed, row retained)."""
        org_id = self.user_context.organization_id
        assert org_id
        existing = await self.repo.get_by_id(
            organization_id=org_id,
            contact_id=contact_id,
            vehicle_id=vehicle_id,
        )
        if not existing:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        status = existing.get("status")
        if status == VehicleStatus.PENDING.value:
            raise ValidationException(
                message_key="contact_onboarding.errors.vehicle_use_withdraw",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if status != VehicleStatus.APPROVED.value:
            raise ValidationException(
                message_key="contact_onboarding.errors.vehicle_remove_not_allowed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if existing.get("parking_slot_id"):
            await self.parking_slots_repo.release_slot(
                organization_id=org_id,
                project_id=existing["project_id"],
                slot_id=existing["parking_slot_id"],
            )
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
        return self._normalize_vehicle(row)

    async def list_project_vehicles(
        self,
        *,
        project_id: str,
        status: VehicleStatus | None = None,
    ) -> list[dict[str, Any]]:
        """List vehicles for a project (admin)."""
        org_id = self.user_context.organization_id
        assert org_id
        rows = await self.repo.list_by_project(
            organization_id=org_id,
            project_id=project_id,
            status=status.value if status else None,
        )
        return [self._normalize_vehicle(row) for row in rows]

    async def review_vehicle(
        self,
        *,
        project_id: str,
        vehicle_id: str,
        body: ReviewVehicleRequest,
    ) -> dict[str, Any]:
        """Approve or reject a vehicle request and assign a parking slot on approval."""
        org_id = self.user_context.organization_id
        assert org_id
        vehicle = await self.repo.get_by_project(
            organization_id=org_id,
            project_id=project_id,
            vehicle_id=vehicle_id,
        )
        if not vehicle:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if vehicle.get("status") != VehicleStatus.PENDING.value:
            raise ValidationException(
                message_key="contact_onboarding.errors.vehicle_not_pending",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        if body.status == VehicleStatus.APPROVED:
            assert body.parking_slot_id
            slot = await self.parking_slots_repo.get_slot(
                organization_id=org_id,
                project_id=project_id,
                slot_id=body.parking_slot_id,
            )
            if not slot or slot.get("status") != "available":
                raise ValidationException(
                    message_key="contact_onboarding.errors.parking_slot_unavailable",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            assigned = await self.parking_slots_repo.assign_slot(
                organization_id=org_id,
                project_id=project_id,
                slot_id=body.parking_slot_id,
            )
            if not assigned:
                raise ValidationException(
                    message_key="contact_onboarding.errors.parking_slot_unavailable",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            row = await self.repo.update_by_project(
                organization_id=org_id,
                project_id=project_id,
                vehicle_id=vehicle_id,
                update_data={
                    "status": VehicleStatus.APPROVED.value,
                    "parking_slot_id": body.parking_slot_id,
                    "rejection_reason": None,
                },
            )
        else:
            row = await self.repo.update_by_project(
                organization_id=org_id,
                project_id=project_id,
                vehicle_id=vehicle_id,
                update_data={
                    "status": VehicleStatus.REJECTED.value,
                    "rejection_reason": body.rejection_reason,
                    "parking_slot_id": None,
                },
            )
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.vehicle_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._normalize_vehicle(row)

    async def complete_vehicles_step(
        self,
        *,
        contact_id: str,
        contact_unit_id: str,
    ) -> None:
        """Mark the vehicles onboarding step complete for one unit."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.contact_units_repo.get_owned_by_contact(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_id=contact_unit_id,
        )
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.contact_unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self.unit_onboarding_repo.complete_step(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_id=contact_unit_id,
            step_key=ContactOnboardingStep.VEHICLES.value,
        )
