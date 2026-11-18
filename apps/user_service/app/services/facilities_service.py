"""Facilities service: CRUD, parking slot provisioning, and step completion."""

from __future__ import annotations

import re
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
    FacilityStatus,
    FacilityType,
    ParkingFacilitySubtype,
    ProjectSetupStep,
    UnitNumberingPattern,
)
from apps.user_service.app.schemas.facility_booking_config import (
    CancellationTier,
    DayHours,
    NightRate,
    UpdateFacilityBookingConfigRequest,
)
from apps.user_service.app.schemas.facility_booking_inventory import (
    CreateBookingUnitRequest,
    UpdateBookingUnitRequest,
)
from apps.user_service.app.schemas.project_inventory import (
    CreateFacilityRequest,
    UpdateFacilityRequest,
)
from apps.user_service.app.services.facility_booking.defaults import (
    default_booking_config,
    suggested_archetype,
)
from apps.user_service.app.services.facility_booking_config_service import (
    FacilityBookingConfigService,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.services.project_setup_validation import (
    normalize_parking_facility_subtype,
    validate_facility_payload,
)
from apps.user_service.app.services.project_workbook_parser import (
    FacilityRowError,
    parse_facilities_template,
)
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.parking_slot_numbering import build_parking_slot_pairs
from apps.user_service.app.utils.project_serialization import (
    serialize_facility_row,
    serialize_row,
)
from libs.shared_utils.http_exceptions import (
    CustomHTTPException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_MAX_WORKBOOK_BYTES = 20 * 1024 * 1024


def _require_xlsx_workbook(payload: bytes, filename: str) -> None:
    """Reject an upload that is empty, too large, or not an xlsx workbook."""
    if not filename.lower().endswith(".xlsx"):
        raise ValidationException(
            message_key="facilities_imports.errors.xlsx_required",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    if not payload or len(payload) > _MAX_WORKBOOK_BYTES:
        raise ValidationException(
            message_key=(
                "facilities_imports.errors.empty_file"
                if not payload
                else "facilities_imports.errors.file_too_large"
            ),
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )


_BOOKING_OVERRIDE_KEYS = (
    "slot_minutes",
    "opens_min",
    "resident_rate",
    "guest_rate",
    "rate_unit",
    "tax_percent",
    "deposit",
    "max_people",
    "advance_booking_days",
    "requires_approval",
    "free_cancel_hours",
    "late_cancel_percent",
)


def _row_has_booking_overrides(row: dict[str, Any]) -> bool:
    """Return whether the sheet filled any booking column."""
    for key in _BOOKING_OVERRIDE_KEYS:
        if row[key] is not None:
            return True
    return bool(row["notes"])


async def read_bounded_upload(upload: Any, *, max_bytes: int = _MAX_WORKBOOK_BYTES) -> bytes:
    """Read an upload in chunks and reject it once it exceeds max_bytes."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        if total + len(chunk) > max_bytes:
            raise ValidationException(
                message_key="facilities_imports.errors.file_too_large",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        chunks.append(chunk)
        total += len(chunk)


def _unique_unit_names(names: list[str]) -> list[str]:
    """Drop blank-normalized duplicate court or room names, keeping sheet order."""
    desired: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        desired.append(name)
    return desired


async def _sync_imported_units(
    booking: FacilityBookingConfigService,
    *,
    project_id: str,
    facility_id: str,
    names: list[str],
    units: list[dict[str, Any]],
) -> None:
    """Match sheet names to existing units, rename the spare ones, and create the rest."""
    desired = _unique_unit_names(names)
    if not desired:
        return
    wanted = {name.casefold() for name in desired}
    spare = [unit for unit in units if str(unit.get("name") or "").casefold() not in wanted]
    next_sort = len(units)
    for index, name in enumerate(desired):
        if _unit_named(units, name):
            continue
        if spare:
            unit = spare.pop(0)
            await booking.update_unit(
                project_id=project_id,
                facility_id=facility_id,
                unit_id=str(unit["id"]),
                body=UpdateBookingUnitRequest(name=name, sort_order=index),
            )
            continue
        await booking.create_unit(
            project_id=project_id,
            facility_id=facility_id,
            body=CreateBookingUnitRequest(name=name, sort_order=next_sort),
        )
        next_sort += 1


def _unit_named(units: list[dict[str, Any]], name: str) -> bool:
    """Return whether a booking unit already uses this name."""
    key = name.casefold()
    for unit in units:
        if str(unit.get("name") or "").casefold() == key:
            return True
    return False


def _booking_pricing(defaults: Any, row: dict[str, Any]) -> Any:
    """Copy default pricing and fill the rate columns that the sheet provided."""
    filled = {
        key: row[source]
        for key, source in (
            ("resident_rate", "resident_rate"),
            ("guest_rate", "guest_rate"),
            ("tax_percent", "tax_percent"),
            ("deposit", "deposit"),
            ("unit_label", "rate_unit"),
        )
        if row[source] is not None
    }
    return defaults.pricing.model_copy(update=filled)


def _booking_policies(defaults: Any, row: dict[str, Any]) -> Any:
    """Copy default policies and fill approval, advance days, and cancellation."""
    updates: dict[str, Any] = {}
    if row["requires_approval"] is not None:
        updates["requires_approval"] = row["requires_approval"]
    if row["advance_booking_days"] is not None:
        updates["advance_booking_days"] = row["advance_booking_days"]
    tiers = _cancellation_tiers(row)
    if tiers is not None:
        updates["cancellation_tiers"] = tiers
    policies = defaults.policies
    if updates:
        policies = policies.model_copy(update=updates)
    return _widen_slot_duration(policies, row["slot_minutes"])


def _cancellation_tiers(row: dict[str, Any]) -> list[CancellationTier] | None:
    """Build cancellation tiers when either cancellation column is filled."""
    if row["free_cancel_hours"] is None and row["late_cancel_percent"] is None:
        return None
    free_hours = row["free_cancel_hours"]
    if free_hours is None:
        free_hours = 24
    late_percent = row["late_cancel_percent"]
    if late_percent is None:
        late_percent = 100
    tiers = []
    if free_hours > 0:
        tiers.append(
            CancellationTier(label="Free cancellation", hours_before=free_hours, fee_percent=0)
        )
    tiers.append(
        CancellationTier(label="Late cancellation", hours_before=0, fee_percent=late_percent)
    )
    return tiers


def _widen_slot_duration(policies: Any, slot_minutes: int | None) -> Any:
    """Widen min and max duration so the sheet slot length is allowed."""
    if slot_minutes is None:
        return policies
    duration: dict[str, int] = {}
    if slot_minutes < policies.min_duration_min:
        duration["min_duration_min"] = slot_minutes
    if slot_minutes > policies.max_duration_min:
        duration["max_duration_min"] = slot_minutes
    if not duration:
        return policies
    return policies.model_copy(update=duration)


def _booking_hours(defaults: Any, row: dict[str, Any]) -> list[DayHours]:
    """Use the sheet open and close time for every day when both are filled."""
    if row["opens_min"] is None or row["closes_min"] is None:
        return defaults.default_hours
    return [
        DayHours(open=row["opens_min"], close=row["closes_min"], closed=False) for _ in range(7)
    ]


def _booking_setup(defaults: Any, row: dict[str, Any]) -> Any:
    """Copy default setup and fill slot, event, and room fields from the sheet."""
    setup = defaults.setup
    updates = {
        "slot": _slot_setup(setup, row),
        "event": _event_setup(setup, row),
        "room": _room_setup(setup, row),
    }
    filled = {key: value for key, value in updates.items() if value is not None}
    if not filled:
        return setup
    return setup.model_copy(update=filled)


def _slot_setup(setup: Any, row: dict[str, Any]) -> Any:
    """Fill slot durations and max players when this archetype has a slot setup."""
    if setup.slot is None:
        return None
    updates: dict[str, Any] = {}
    if row["slot_minutes"] is not None:
        updates["durations"] = [row["slot_minutes"]]
    if row["max_people"] is not None:
        updates["max_players"] = row["max_people"]
    if not updates:
        return None
    return setup.slot.model_copy(update=updates)


def _event_setup(setup: Any, row: dict[str, Any]) -> Any:
    """Fill event capacity from max people, then from the facility capacity."""
    participants = row["max_people"] or row["capacity_persons"]
    if setup.event is None or not participants:
        return None
    return setup.event.model_copy(update={"max_participants": participants})


def _room_setup(setup: Any, row: dict[str, Any]) -> Any:
    """Fill the room night rate from the resident rate."""
    if setup.room is None or row["resident_rate"] is None:
        return None
    return setup.room.model_copy(
        update={"night_rates": [NightRate(min_nights=1, rate=row["resident_rate"])]}
    )


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
        data["photo_paths"] = list(body.photo_paths)
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
        if body.photo_paths is not None:
            data["photo_paths"] = list(body.photo_paths)
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

    async def import_facilities(
        self, *, project_id: str, payload: bytes, filename: str
    ) -> dict[str, Any]:
        """Create or update facilities from an uploaded Facilities template workbook."""
        await self.setup_service.ensure_project(project_id=project_id)
        _require_xlsx_workbook(payload, filename)
        try:
            rows, parse_errors, fatal = parse_facilities_template(payload)
        except ValueError as exc:
            raise ValidationException(
                message_key=str(exc),
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            ) from exc
        if fatal:
            raise ValidationException(
                message_key=fatal,
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        existing = await self._existing_facilities(project_id=project_id)
        towers = await self._towers_by_key(project_id=project_id)
        created = updated = 0
        errors = [
            {"row_number": item.row_number, "code": item.code, "message": item.message}
            for item in parse_errors
        ]
        for row in rows:
            facility_id = existing.get(row["name"].casefold())
            try:
                async with self.db_connection.transaction():
                    await self._save_imported_row(
                        project_id=project_id, row=row, towers=towers, facility_id=facility_id
                    )
            except FacilityRowError as exc:
                errors.append(
                    {"row_number": row["sheet_row"], "code": exc.code, "message": exc.message}
                )
            except CustomHTTPException as exc:
                errors.append(
                    {
                        "row_number": row["sheet_row"],
                        "code": exc.message_key,
                        "message": exc.message_key,
                    }
                )
            else:
                if facility_id:
                    updated += 1
                else:
                    created += 1
        return {"created": created, "updated": updated, "errors": errors}

    async def _save_imported_row(
        self,
        *,
        project_id: str,
        row: dict[str, Any],
        towers: dict[str, list[dict[str, Any]]],
        facility_id: str | None,
    ) -> None:
        """Create a new facility, or update the one that already uses this name."""
        if facility_id:
            await self._update_imported_facility(
                project_id=project_id, facility_id=facility_id, row=row, towers=towers
            )
            return
        await self._import_facility_row(project_id=project_id, row=row, towers=towers)

    async def _update_imported_facility(
        self,
        *,
        project_id: str,
        facility_id: str,
        row: dict[str, Any],
        towers: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Replace an existing facility with the values from one imported row."""
        location_type, tower_id = self._imported_location(row, towers)
        body = self._imported_facility_body(row, location_type, tower_id)
        await self.update_facility(
            project_id=project_id,
            facility_id=facility_id,
            body=UpdateFacilityRequest(**body.model_dump()),
        )
        if row["is_bookable"]:
            await self._apply_imported_booking(project_id, facility_id, row)

    async def _existing_facilities(self, *, project_id: str) -> dict[str, str]:
        """Map existing facility names to their ids."""
        found: dict[str, str] = {}
        page = 1
        while True:
            listed = await self.list_facilities(project_id=project_id, page=page, page_size=100)
            items = listed.get("items") or []
            for item in items:
                name = str(item.get("name") or "").casefold()
                if name and name not in found:
                    found[name] = str(item.get("id") or "")
            if not items or page * 100 >= int(listed.get("total") or 0):
                return found
            page += 1

    async def _towers_by_key(self, *, project_id: str) -> dict[str, list[dict[str, Any]]]:
        """Index project towers by normalized name and code."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for tower in await self.towers_repo.list_towers(
            organization_id=self._org_id, project_id=project_id
        ):
            row = serialize_row(tower)
            for raw in (row.get("name"), row.get("code")):
                key = " ".join(str(raw or "").split()).casefold()
                if key:
                    grouped.setdefault(key, []).append(row)
        return grouped

    async def _import_facility_row(
        self, *, project_id: str, row: dict[str, Any], towers: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Create one parsed facility and apply booking columns when residents can book."""
        location_type, tower_id = self._imported_location(row, towers)
        created = await self.create_facility(
            project_id=project_id,
            body=self._imported_facility_body(row, location_type, tower_id),
        )
        if row["is_bookable"]:
            await self._apply_imported_booking(project_id, str(created["id"]), row)

    def _imported_location(
        self, row: dict[str, Any], towers: dict[str, list[dict[str, Any]]]
    ) -> tuple[FacilityLocationType, str | None]:
        """Map Tower / Block and Indoor / Outdoor onto a facility location."""
        tower = self._match_tower(row, towers)
        if tower:
            return FacilityLocationType.IN_TOWER, str(tower["id"])
        if row["indoor"] is True:
            return FacilityLocationType.INDOOR_CLUBHOUSE, None
        if row["indoor"] is False:
            return FacilityLocationType.OUTDOOR_STANDALONE, None
        raise FacilityRowError(
            "location_required",
            "Indoor / Outdoor is required when Tower / Block is blank.",
        )

    def _imported_facility_body(
        self,
        row: dict[str, Any],
        location_type: FacilityLocationType,
        tower_id: str | None,
    ) -> CreateFacilityRequest:
        """Build the create-facility request for one imported row."""
        subtype = row["facility_subtype"]
        if row["facility_type"] == FacilityType.PARKING:
            subtype = self._parking_subtype(row)
        payload: dict[str, Any] = {
            "name": row["name"],
            "status": row["status"],
            "facility_type": row["facility_type"],
            "facility_subtype": subtype,
            "location_type": location_type,
            "tower_id": tower_id,
            "floor_level": row["floor_level"],
            "area_sqft": row["area_sqft"],
            "capacity_persons": row["capacity_persons"],
            "location_notes": row["location_notes"],
            "active": row["status"] == FacilityStatus.ACTIVE,
            "sort_order": row["sort_order"],
            "is_bookable": row["is_bookable"],
            "booking_archetype": row["booking_archetype"],
        }
        if row["facility_type"] == FacilityType.PARKING:
            payload.update(
                parking_slots=row["parking_slots"],
                parking_user_type=row["parking_user_type"],
                parking_vehicle_category=row["parking_vehicle_category"],
            )
        return CreateFacilityRequest(**payload)

    async def _apply_imported_booking(
        self, project_id: str, facility_id: str, row: dict[str, Any]
    ) -> None:
        """Write booking columns and reconcile court or room names for this row."""
        booking = FacilityBookingConfigService(
            db_connection=self.db_connection, user_context=self.user_context
        )
        workspace = await booking.get_workspace(project_id=project_id, facility_id=facility_id)
        if _row_has_booking_overrides(row):
            await booking.update_config(
                project_id=project_id,
                facility_id=facility_id,
                body=self._booking_update(row, version=int(workspace["config"]["version"])),
            )
        await _sync_imported_units(
            booking,
            project_id=project_id,
            facility_id=facility_id,
            names=list(row["unit_names"]),
            units=list(workspace.get("units") or []),
        )

    @staticmethod
    def _match_tower(
        row: dict[str, Any], towers: dict[str, list[dict[str, Any]]]
    ) -> dict[str, Any] | None:
        """Match Tower / Block to one project tower."""
        if not row["tower_name"]:
            return None
        key = " ".join(str(row["tower_name"]).split()).casefold()
        matches: list[dict[str, Any]] = []
        seen: set[str] = set()
        for tower in towers.get(key, []):
            identity = str(tower.get("id") or "")
            if identity in seen:
                continue
            seen.add(identity)
            matches.append(tower)
        if not matches:
            raise FacilityRowError(
                "tower_not_found",
                f"Tower / Block '{row['tower_name']}' was not found on this project.",
            )
        if len(matches) > 1:
            raise FacilityRowError(
                "tower_ambiguous",
                f"More than one tower matches '{row['tower_name']}'.",
            )
        if bool(matches[0].get("has_wings")):
            raise FacilityRowError(
                "tower_wing_required",
                (
                    f"Tower '{row['tower_name']}' uses wings. This template has no wing column, "
                    "so this row cannot be placed in that tower."
                ),
            )
        return matches[0]

    @staticmethod
    def _parking_subtype(row: dict[str, Any]) -> str:
        """Resolve a parking subtype from Sport / Sub-type, then location notes."""
        for candidate in (row["parking_subtype_hint"], row["location_notes"]):
            if not candidate:
                continue
            try:
                return normalize_parking_facility_subtype(candidate, required=True) or ""
            except ValidationException:
                text = str(candidate).casefold()
            if re.search(r"\bev\b", text) or "ev_charging" in text or "ev-charging" in text:
                return ParkingFacilitySubtype.EV_CHARGING.value
            for keyword, subtype in (
                ("basement", ParkingFacilitySubtype.BASEMENT.value),
                ("stilt", ParkingFacilitySubtype.STILT.value),
                ("podium", ParkingFacilitySubtype.PODIUM.value),
                ("open", ParkingFacilitySubtype.OPEN.value),
                ("covered", ParkingFacilitySubtype.COVERED.value),
            ):
                if keyword in text:
                    return subtype
        raise FacilityRowError(
            "parking_subtype_required",
            (
                "Parking subtype is required. Put covered, open, basement, stilt, podium, "
                "or EV charging in Sport / Sub-type."
            ),
        )

    @staticmethod
    def _booking_update(row: dict[str, Any], *, version: int) -> UpdateFacilityBookingConfigRequest:
        """Overlay filled booking columns onto the archetype defaults."""
        archetype = row["booking_archetype"] or suggested_archetype(row["facility_type"].value)
        defaults = default_booking_config(archetype)
        return UpdateFacilityBookingConfigRequest(
            version=version,
            description=row["notes"],
            slot_minutes=row["slot_minutes"],
            default_hours=_booking_hours(defaults, row),
            pricing=_booking_pricing(defaults, row),
            policies=_booking_policies(defaults, row),
            setup=_booking_setup(defaults, row),
        )
