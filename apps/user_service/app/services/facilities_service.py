"""Facilities service: CRUD, parking slot provisioning, and step completion."""

from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.facilities_repository import (
    FacilitiesRepository,
)
from apps.user_service.app.db.repositories.facility_booking_config_repository import (
    FacilityBookingConfigRepository,
)
from apps.user_service.app.db.repositories.facility_booking_inventory_repository import (
    FacilityBookingInventoryRepository,
)
from apps.user_service.app.db.repositories.facility_reservations_repository import (
    FacilityReservationsRepository,
)
from apps.user_service.app.db.repositories.facility_staff_assignments_repository import (
    FacilityStaffAssignmentsRepository,
)
from apps.user_service.app.db.repositories.parking_slots_repository import (
    ParkingSlotsRepository,
)
from apps.user_service.app.db.repositories.towers_repository import TowersRepository
from apps.user_service.app.schemas.enums import (
    FacilityBookingArchetype,
    FacilityLocationType,
    FacilityType,
    ProjectSetupStep,
    UnitNumberingPattern,
)
from apps.user_service.app.schemas.project_inventory import (
    CreateFacilityRequest,
    UpdateFacilityRequest,
)
from apps.user_service.app.services.facility_booking.defaults import (
    default_booking_config,
    suggested_archetype,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.services.project_setup_validation import (
    validate_facility_payload,
)
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.parking_slot_numbering import build_parking_slot_pairs
from apps.user_service.app.utils.project_serialization import (
    serialize_facility_row,
    serialize_row,
)
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
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
        self.towers_repo = TowersRepository(db_connection)
        self.booking_config_repo = FacilityBookingConfigRepository(db_connection)
        self.booking_inventory_repo = FacilityBookingInventoryRepository(db_connection)
        self.reservations_repo = FacilityReservationsRepository(db_connection)
        self.staff_assignments_repo = FacilityStaffAssignmentsRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    def _serialize_create_facility(self, body: CreateFacilityRequest) -> dict[str, Any]:
        """Map create request to DB-ready dict."""
        data = body.model_dump(
            exclude={"numbering_pattern", "starting_slots_number", "custom_prefix"}
        )
        data["status"] = body.status.value
        data["facility_type"] = body.facility_type.value
        data["location_type"] = body.location_type.value
        if body.parking_user_type:
            data["parking_user_type"] = body.parking_user_type.value
        if body.parking_vehicle_category:
            data["parking_vehicle_category"] = body.parking_vehicle_category.value
        if body.facility_type == FacilityType.PARKING:
            data["numbering_pattern"] = (
                body.numbering_pattern or UnitNumberingPattern.FLOOR_UNIT
            ).value
            data["starting_slots_number"] = (
                1 if body.starting_slots_number is None else body.starting_slots_number
            )
            data["custom_prefix"] = body.custom_prefix
        data["extra_attributes"] = body.extra_attributes or {}
        if body.booking_archetype:
            data["booking_archetype"] = body.booking_archetype.value
        return data

    def _serialize_update_facility(self, body: UpdateFacilityRequest) -> dict[str, Any]:
        """Map patch request to DB-ready dict."""
        data = body.model_dump(exclude_unset=True, exclude_none=True)
        if body.status:
            data["status"] = body.status.value
        if body.facility_type:
            data["facility_type"] = body.facility_type.value
        if body.location_type:
            data["location_type"] = body.location_type.value
        if body.parking_user_type:
            data["parking_user_type"] = body.parking_user_type.value
        if body.parking_vehicle_category:
            data["parking_vehicle_category"] = body.parking_vehicle_category.value
        if body.numbering_pattern:
            data["numbering_pattern"] = body.numbering_pattern.value
        if body.booking_archetype:
            data["booking_archetype"] = body.booking_archetype.value
        return data

    async def _resolve_tower_has_wings(
        self,
        *,
        project_id: str,
        data: dict[str, Any],
    ) -> bool | None:
        """Return whether the referenced tower uses wings, when location is in_tower."""
        location_type = data.get("location_type")
        if isinstance(location_type, FacilityLocationType):
            location_type = location_type.value
        if location_type != FacilityLocationType.IN_TOWER.value:
            return None

        tower_id = data.get("tower_id")
        if not tower_id:
            return None

        tower = await self.towers_repo.get_tower(
            organization_id=self._org_id,
            project_id=project_id,
            tower_id=str(tower_id),
        )
        if not tower:
            raise NotFoundException(
                message_key="project_setup.errors.tower_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return bool(tower.get("has_wings"))

    @staticmethod
    def _reject_non_parking_numbering(body: CreateFacilityRequest) -> None:
        """Numbering fields are only allowed on parking facilities."""
        if body.facility_type == FacilityType.PARKING:
            return
        if (
            body.numbering_pattern is not None
            or body.starting_slots_number is not None
            or body.custom_prefix is not None
        ):
            raise ValidationException(
                message_key="project_setup.errors.facility_parking_numbering_not_applicable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

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
        facility_data: dict[str, Any],
        slot_count: int,
    ) -> None:
        """Create numbered parking slots for a parking facility."""
        starting_slots_number = int(facility_data.get("starting_slots_number") or 1)
        slots = build_parking_slot_pairs(
            slot_count=slot_count,
            starting_slots_number=starting_slots_number,
            numbering_pattern=str(
                facility_data.get("numbering_pattern") or UnitNumberingPattern.FLOOR_UNIT.value
            ),
            custom_prefix=facility_data.get("custom_prefix"),
            floor_level=facility_data.get("floor_level"),
        )
        await self.parking_slots_repo.bulk_insert_slots(
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=facility_id,
            slots=slots,
        )

    async def create_facility(
        self, *, project_id: str, body: CreateFacilityRequest
    ) -> dict[str, Any]:
        """Create a facility and provision parking slots when applicable."""
        await self.setup_service.ensure_project(project_id=project_id)
        self._reject_non_parking_numbering(body)
        data = self._serialize_create_facility(body)
        tower_has_wings = await self._resolve_tower_has_wings(project_id=project_id, data=data)
        validate_facility_payload(data, tower_has_wings=tower_has_wings)
        data["organization_id"] = self._org_id
        data["project_id"] = project_id
        inserted = await self.facilities_repo.insert_facility(data)
        if data.get("is_bookable"):
            await self._provision_booking_config(
                project_id=project_id,
                facility=inserted,
                capacity_persons=body.capacity_persons,
            )
        if body.facility_type == FacilityType.PARKING and body.parking_slots:
            await self._provision_parking_slots(
                project_id=project_id,
                facility_id=str(inserted["id"]),
                facility_data=data,
                slot_count=body.parking_slots,
            )
        return serialize_facility_row(inserted)

    async def list_facilities(
        self,
        *,
        project_id: str,
        facility_types: list[str] | None = None,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
        is_bookable: bool | None = None,
    ) -> dict[str, Any]:
        """List facilities for a project."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows, total = await self.facilities_repo.list_facilities(
            organization_id=self._org_id,
            project_id=project_id,
            facility_types=facility_types,
            status=status,
            search=search,
            is_bookable=is_bookable,
            page=page,
            page_size=page_size,
        )
        items = [serialize_facility_row(row) for row in rows]
        return {"items": items, "total": total}

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
        await self._guard_booking_change(
            project_id=project_id, facility_id=facility_id, current=current, merged=merged
        )
        tower_has_wings = await self._resolve_tower_has_wings(
            project_id=project_id,
            data=merged,
        )
        validate_facility_payload(merged, tower_has_wings=tower_has_wings)
        updated = await self.facilities_repo.update_facility(
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=facility_id,
            update_data=patch,
        )
        if merged.get("is_bookable"):
            existing = await self.booking_config_repo.get_config(
                organization_id=self._org_id,
                project_id=project_id,
                facility_id=facility_id,
            )
            if not existing:
                await self._provision_booking_config(
                    project_id=project_id,
                    facility=updated or merged,
                    capacity_persons=merged.get("capacity_persons"),
                )
            elif self._archetype_changed(current, merged):
                await self._reset_booking_config(
                    project_id=project_id,
                    facility_id=facility_id,
                    archetype=str(merged.get("booking_archetype")),
                    existing=existing,
                )
        return serialize_facility_row(updated or {})

    async def delete_facility(self, *, project_id: str, facility_id: str) -> dict[str, Any]:
        """Delete a facility and its parking slots."""
        current = await self._ensure_facility(project_id=project_id, facility_id=facility_id)
        upcoming = await self.reservations_repo.count_upcoming_active(
            organization_id=self._org_id,
            facility_id=facility_id,
            from_date=date.today(),
        )
        if upcoming:
            raise ValidationException(
                message_key="project_setup.errors.facility_has_upcoming_bookings",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self.staff_assignments_repo.remove_facility(
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=facility_id,
        )
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
        return {"old_data": serialize_facility_row(current), "new_data": None}

    async def complete_facilities(self, *, project_id: str) -> dict[str, Any]:
        """Mark the facilities step complete."""
        return await self.setup_service.complete_step(
            project_id=project_id,
            step_key=ProjectSetupStep.FACILITIES.value,
        )

    @staticmethod
    def _archetype_of(row: dict[str, Any]) -> str | None:
        """Return booking archetype string from a facility row."""
        value = row.get("booking_archetype")
        if hasattr(value, "value"):
            return str(value.value)
        return str(value) if value else None

    def _archetype_changed(self, current: dict[str, Any], merged: dict[str, Any]) -> bool:
        """Return whether the booking archetype changed between rows."""
        return self._archetype_of(current) != self._archetype_of(merged)

    async def _guard_booking_change(
        self,
        *,
        project_id: str,
        facility_id: str,
        current: dict[str, Any],
        merged: dict[str, Any],
    ) -> None:
        """Block disabling booking or changing archetype when upcoming reservations exist."""
        _ = project_id
        turning_off = current.get("is_bookable") and not merged.get("is_bookable")
        changing_type = bool(merged.get("is_bookable")) and self._archetype_changed(current, merged)
        if not turning_off and not changing_type:
            return
        upcoming = await self.reservations_repo.count_upcoming_active(
            organization_id=self._org_id,
            facility_id=facility_id,
            from_date=date.today(),
        )
        if upcoming:
            raise ValidationException(
                message_key="project_setup.errors.facility_has_upcoming_bookings",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    async def _provision_booking_config(
        self,
        *,
        project_id: str,
        facility: dict[str, Any],
        capacity_persons: int | None,
    ) -> None:
        """Insert default booking config and a primary unit when booking is enabled."""
        facility_id = str(facility["id"])
        archetype = (
            self._archetype_of(facility)
            or suggested_archetype(str(facility.get("facility_type") or "")).value
        )
        defaults = default_booking_config(archetype)
        setup = defaults.setup
        if setup.event and capacity_persons:
            setup = setup.model_copy(
                update={
                    "event": setup.event.model_copy(
                        update={"max_participants": int(capacity_persons)}
                    )
                }
            )
        await self.booking_config_repo.insert_config(
            {
                "organization_id": self._org_id,
                "project_id": project_id,
                "facility_id": facility_id,
                "description": "",
                "slot_minutes": defaults.slot_minutes,
                "accepting_bookings": True,
                "default_hours": [h.model_dump(mode="json") for h in defaults.default_hours],
                "pricing": defaults.pricing.model_dump(mode="json"),
                "policies": defaults.policies.model_dump(mode="json"),
                "setup": setup.model_dump(mode="json"),
                "created_by_user_id": self.user_context.user_id,
                "updated_by_user_id": self.user_context.user_id,
            }
        )
        await self.booking_inventory_repo.insert(
            "facility_booking_units",
            organization_id=self._org_id,
            project_id=project_id,
            facility_id=facility_id,
            data={"name": self._default_unit_name(archetype, facility.get("name"))},
        )

    async def _reset_booking_config(
        self,
        *,
        project_id: str,
        facility_id: str,
        archetype: str,
        existing: dict[str, Any],
    ) -> None:
        """Reset booking config to archetype defaults after a type change."""
        _ = project_id
        defaults = default_booking_config(archetype)
        await self.booking_config_repo.update_config(
            organization_id=self._org_id,
            facility_id=facility_id,
            expected_version=int(existing["version"]),
            update_data={
                "slot_minutes": defaults.slot_minutes,
                "default_hours": [h.model_dump(mode="json") for h in defaults.default_hours],
                "pricing": defaults.pricing.model_dump(mode="json"),
                "policies": defaults.policies.model_dump(mode="json"),
                "setup": defaults.setup.model_dump(mode="json"),
                "updated_by_user_id": self.user_context.user_id,
            },
        )

    @staticmethod
    def _default_unit_name(archetype: str, facility_name: Any) -> str:
        """Return the default primary unit label for a booking archetype."""
        labels = {
            FacilityBookingArchetype.SLOT.value: "Court 1",
            FacilityBookingArchetype.TEE_TIME.value: "Tee sheet",
            FacilityBookingArchetype.ROOM.value: "Room 1",
        }
        if archetype in labels:
            return labels[archetype]
        name = str(facility_name or "").strip()
        return name or "Main space"
