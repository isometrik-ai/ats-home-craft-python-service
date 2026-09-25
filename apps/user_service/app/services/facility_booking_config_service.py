"""Staff configuration of bookable facilities: rules, inventory and settings."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.facility_booking_config_repository import (
    FacilityBookingConfigRepository,
)
from apps.user_service.app.db.repositories.facility_booking_inventory_repository import (
    FacilityBookingInventoryRepository,
)
from apps.user_service.app.db.repositories.facility_staff_assignments_repository import (
    FacilityStaffAssignmentsRepository,
)
from apps.user_service.app.schemas.enums import FACILITY_BOOKING_MAX_UNITS
from apps.user_service.app.schemas.facility_booking import (
    BookableFacilityResponse,
    BookingUnitSummary,
)
from apps.user_service.app.schemas.facility_booking_config import (
    FacilityBookingConfigResponse,
    UpdateFacilityBookingConfigRequest,
)
from apps.user_service.app.schemas.facility_booking_inventory import (
    CreateBookingUnitRequest,
    CreateClosureRequest,
    CreateMaintenanceWindowRequest,
    CreateSchedulePeriodRequest,
    CreateSlotBlockRequest,
    UpdateBookingUnitRequest,
    UpdateProjectBookingSettingsRequest,
    UpdateSchedulePeriodRequest,
    UpsertStaffAssignmentRequest,
)
from apps.user_service.app.services.facility_booking.defaults import (
    ensure_archetype_setup,
    normalize_setup,
)
from apps.user_service.app.services.facility_booking.snapshot import snapshot_from_rows
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_TABLE_NOT_FOUND = {
    "facility_booking_units": "facility_booking.errors.unit_not_found",
    "facility_schedule_periods": "facility_booking.errors.schedule_not_found",
    "facility_slot_blocks": "facility_booking.errors.block_not_found",
    "facility_closures": "facility_booking.errors.closure_not_found",
    "facility_maintenance_windows": "facility_booking.errors.maintenance_not_found",
}


def _sid(value: Any) -> str | None:
    """Coerce a value to string id or None."""
    return str(value) if value is not None else None


class FacilityBookingConfigService:
    """CRUD for booking config, inventory, staff assignments and project settings."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.config_repo = FacilityBookingConfigRepository(db_connection)
        self.inventory_repo = FacilityBookingInventoryRepository(db_connection)
        self.staff_repo = FacilityStaffAssignmentsRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    async def _ensure_project(self, project_id: str) -> None:
        """Ensure the project exists before config operations."""
        await self.setup_service.ensure_project(project_id=project_id)

    async def _require_config(self, *, project_id: str, facility_id: str) -> dict[str, Any]:
        """Return booking config row or raise 404."""
        await self._ensure_project(project_id)
        row = await self.config_repo.get_config(
            organization_id=self._org_id, project_id=project_id, facility_id=facility_id
        )
        if not row:
            raise NotFoundException(
                message_key="facility_booking.errors.config_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    async def timezone_for(self, project_id: str) -> str:
        """Resolve IANA timezone for a project (settings, org default, or UTC)."""
        settings = await self.config_repo.get_settings(
            organization_id=self._org_id, project_id=project_id
        )
        if settings and settings.get("timezone"):
            return str(settings["timezone"])
        org_tz = await self.config_repo.get_organization_timezone(self._org_id)
        return org_tz or "UTC"

    async def local_now(self, project_id: str) -> datetime:
        """Return current local time in the project timezone (naive datetime)."""
        name = await self.timezone_for(project_id)
        try:
            zone = ZoneInfo(name)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        return datetime.now(timezone.utc).astimezone(zone).replace(tzinfo=None)

    def _serialize_config(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a config DB row to the API response shape."""
        return FacilityBookingConfigResponse(
            id=str(row["id"]),
            facility_id=str(row["facility_id"]),
            facility_name=str(row.get("facility_name") or ""),
            facility_type=str(row.get("facility_type") or ""),
            archetype=row["archetype"],
            description=row.get("description") or "",
            slot_minutes=int(row["slot_minutes"]),
            accepting_bookings=bool(row["accepting_bookings"]),
            default_hours=row["default_hours"],
            pricing=row["pricing"],
            policies=row["policies"],
            setup=row["setup"],
            policies_document=row.get("policies_document"),
            version=int(row["version"]),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        ).model_dump(mode="json")

    def _catalog_card(self, row: dict[str, Any], units: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a resident/staff catalog card for one bookable facility."""
        policies = row.get("policies") or {}
        pricing = row.get("pricing") or {}
        setup = row.get("setup") or {}
        return BookableFacilityResponse(
            id=str(row["facility_id"]),
            name=str(row.get("facility_name") or ""),
            facility_type=str(row.get("facility_type") or ""),
            archetype=row["archetype"],
            description=row.get("description") or "",
            slot_minutes=int(row["slot_minutes"]),
            accepting_bookings=bool(row.get("accepting_bookings", True)),
            requires_approval=bool(policies.get("requires_approval")),
            advance_booking_days=int(policies.get("advance_booking_days") or 0),
            price_mode=str(setup.get("price_mode") or "fixed"),
            resident_rate=int(pricing.get("resident_rate") or 0),
            unit_label=str(pricing.get("unit_label") or "hour"),
            units=[
                BookingUnitSummary(
                    id=str(unit["id"]),
                    name=unit["name"],
                    room_type=unit.get("room_type"),
                )
                for unit in units
                if unit.get("active", True)
            ],
            tower_id=_sid(row.get("facility_tower_id")),
            location_notes=row.get("facility_location_notes"),
        ).model_dump(mode="json")

    async def list_bookable_facilities(
        self, *, project_id: str, resident_visible_only: bool = False
    ) -> list[dict[str, Any]]:
        """List bookable facilities with summary pricing and units."""
        await self._ensure_project(project_id)
        rows = await self.config_repo.list_configs(
            organization_id=self._org_id,
            project_id=project_id,
            resident_visible_only=resident_visible_only,
        )
        cards: list[dict[str, Any]] = []
        for row in rows:
            units = await self.inventory_repo.list_rows(
                "facility_booking_units",
                organization_id=self._org_id,
                facility_id=str(row["facility_id"]),
            )
            cards.append(self._catalog_card(row, units))
        return cards

    async def get_workspace(self, *, project_id: str, facility_id: str) -> dict[str, Any]:
        """Return full booking workspace: config plus all inventory rows."""
        config = await self._require_config(project_id=project_id, facility_id=facility_id)
        inventory = await self.inventory_repo.load_all(
            organization_id=self._org_id, facility_id=facility_id
        )
        snapshot = snapshot_from_rows(config, inventory)
        return {
            "config": self._serialize_config(config),
            "units": inventory["facility_booking_units"],
            "schedules": inventory["facility_schedule_periods"],
            "slot_blocks": inventory["facility_slot_blocks"],
            "closures": inventory["facility_closures"],
            "maintenance": inventory["facility_maintenance_windows"],
            "unit_count": len(snapshot.units),
        }

    def _apply_config_fields(
        self,
        patch: dict[str, Any],
        *,
        current: dict[str, Any],
        body: UpdateFacilityBookingConfigRequest,
    ) -> None:
        """Merge optional config sections from the request into the update patch."""
        if body.description is not None:
            patch["description"] = body.description
        if body.slot_minutes is not None:
            patch["slot_minutes"] = body.slot_minutes
        if body.accepting_bookings is not None:
            patch["accepting_bookings"] = body.accepting_bookings
        if body.default_hours is not None:
            patch["default_hours"] = [h.model_dump(mode="json") for h in body.default_hours]
        if body.pricing is not None:
            patch["pricing"] = body.pricing.model_dump(mode="json")
        if body.policies is not None:
            patch["policies"] = body.policies.model_dump(mode="json")
        if body.setup is not None:
            setup = ensure_archetype_setup(current["archetype"], normalize_setup(body.setup))
            patch["setup"] = setup.model_dump(mode="json")
            if setup.slot and setup.slot.durations:
                patch["slot_minutes"] = setup.slot.durations[0]

    @staticmethod
    def _apply_policies_document(
        patch: dict[str, Any], body: UpdateFacilityBookingConfigRequest
    ) -> None:
        """Set or clear the uploaded policies document on the patch."""
        if body.clear_policies_document:
            patch["policies_document"] = None
        elif body.policies_document is not None:
            doc = body.policies_document.model_dump(mode="json")
            doc["uploaded_at"] = datetime.now(timezone.utc).isoformat()
            patch["policies_document"] = doc

    async def update_config(
        self,
        *,
        project_id: str,
        facility_id: str,
        body: UpdateFacilityBookingConfigRequest,
    ) -> dict[str, Any]:
        """Patch facility booking config with optimistic concurrency."""
        current = await self._require_config(project_id=project_id, facility_id=facility_id)
        patch: dict[str, Any] = {"updated_by_user_id": self.user_context.user_id}
        self._apply_config_fields(patch, current=current, body=body)
        self._apply_policies_document(patch, body)
        updated = await self.config_repo.update_config(
            organization_id=self._org_id,
            facility_id=facility_id,
            expected_version=body.version,
            update_data=patch,
        )
        if updated is None:
            raise ConflictException(
                message_key="facility_booking.errors.config_version_conflict",
                custom_code=CustomStatusCode.CONFLICT,
            )
        return await self.get_workspace(project_id=project_id, facility_id=facility_id)

    async def _insert_inventory(
        self, *, project_id: str, facility_id: str, table: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Insert an inventory row after verifying config exists."""
        await self._require_config(project_id=project_id, facility_id=facility_id)
        try:
            return await self.inventory_repo.insert(
                table,
                organization_id=self._org_id,
                project_id=project_id,
                facility_id=facility_id,
                data=data,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="facility_booking.errors.duplicate_inventory",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        except asyncpg.ExclusionViolationError as exc:
            raise ConflictException(
                message_key="facility_booking.errors.schedule_overlap",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc

    async def create_unit(
        self, *, project_id: str, facility_id: str, body: CreateBookingUnitRequest
    ) -> dict[str, Any]:
        """Create a bookable unit under the facility."""
        await self._require_config(project_id=project_id, facility_id=facility_id)
        count = await self.inventory_repo.count_units(
            organization_id=self._org_id, facility_id=facility_id
        )
        if count >= FACILITY_BOOKING_MAX_UNITS:
            raise ValidationException(
                message_key="facility_booking.errors.too_many_units",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return await self._insert_inventory(
            project_id=project_id,
            facility_id=facility_id,
            table="facility_booking_units",
            data=body.model_dump(mode="json"),
        )

    async def update_unit(
        self,
        *,
        project_id: str,
        facility_id: str,
        unit_id: str,
        body: UpdateBookingUnitRequest,
    ) -> dict[str, Any]:
        """Update a bookable unit."""
        await self._require_config(project_id=project_id, facility_id=facility_id)
        patch = body.model_dump(exclude_unset=True, mode="json")
        if not patch:
            row = await self.inventory_repo.get(
                "facility_booking_units",
                organization_id=self._org_id,
                facility_id=facility_id,
                row_id=unit_id,
            )
            if not row:
                raise NotFoundException(
                    message_key=_TABLE_NOT_FOUND["facility_booking_units"],
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
            return row
        try:
            row = await self.inventory_repo.update(
                "facility_booking_units",
                organization_id=self._org_id,
                facility_id=facility_id,
                row_id=unit_id,
                update_data=patch,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="facility_booking.errors.duplicate_inventory",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        if not row:
            raise NotFoundException(
                message_key=_TABLE_NOT_FOUND["facility_booking_units"],
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    async def create_schedule(
        self, *, project_id: str, facility_id: str, body: CreateSchedulePeriodRequest
    ) -> dict[str, Any]:
        """Create a seasonal schedule period."""
        data = body.model_dump(mode="json")
        data["hours"] = [h.model_dump(mode="json") for h in body.hours]
        data["created_by_user_id"] = self.user_context.user_id
        return await self._insert_inventory(
            project_id=project_id,
            facility_id=facility_id,
            table="facility_schedule_periods",
            data=data,
        )

    async def update_schedule(
        self,
        *,
        project_id: str,
        facility_id: str,
        period_id: str,
        body: UpdateSchedulePeriodRequest,
    ) -> dict[str, Any]:
        """Update a schedule period."""
        await self._require_config(project_id=project_id, facility_id=facility_id)
        current = await self.inventory_repo.get(
            "facility_schedule_periods",
            organization_id=self._org_id,
            facility_id=facility_id,
            row_id=period_id,
        )
        if not current:
            raise NotFoundException(
                message_key=_TABLE_NOT_FOUND["facility_schedule_periods"],
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        patch = body.model_dump(exclude_unset=True, mode="json")
        if body.hours is not None:
            patch["hours"] = [h.model_dump(mode="json") for h in body.hours]
        starts = patch.get("starts_on", current["starts_on"])
        ends = patch.get("ends_on", current["ends_on"])
        if isinstance(starts, str):
            starts = date.fromisoformat(starts)
        if isinstance(ends, str):
            ends = date.fromisoformat(ends)
        if ends < starts:
            raise ValidationException(
                message_key="facility_booking.errors.invalid_date_range",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        try:
            row = await self.inventory_repo.update(
                "facility_schedule_periods",
                organization_id=self._org_id,
                facility_id=facility_id,
                row_id=period_id,
                update_data=patch,
            )
        except asyncpg.ExclusionViolationError as exc:
            raise ConflictException(
                message_key="facility_booking.errors.schedule_overlap",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        return row or current

    async def create_slot_block(
        self, *, project_id: str, facility_id: str, body: CreateSlotBlockRequest
    ) -> dict[str, Any]:
        """Block slots on the facility calendar."""
        if body.unit_id:
            unit = await self.inventory_repo.get(
                "facility_booking_units",
                organization_id=self._org_id,
                facility_id=facility_id,
                row_id=body.unit_id,
            )
            if not unit:
                raise NotFoundException(
                    message_key=_TABLE_NOT_FOUND["facility_booking_units"],
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
        data = body.model_dump(mode="json")
        data["created_by_user_id"] = self.user_context.user_id
        return await self._insert_inventory(
            project_id=project_id,
            facility_id=facility_id,
            table="facility_slot_blocks",
            data=data,
        )

    async def create_closure(
        self, *, project_id: str, facility_id: str, body: CreateClosureRequest
    ) -> dict[str, Any]:
        """Mark the facility closed on a date."""
        data = body.model_dump(mode="json")
        data["created_by_user_id"] = self.user_context.user_id
        return await self._insert_inventory(
            project_id=project_id,
            facility_id=facility_id,
            table="facility_closures",
            data=data,
        )

    async def create_maintenance(
        self, *, project_id: str, facility_id: str, body: CreateMaintenanceWindowRequest
    ) -> dict[str, Any]:
        """Add a maintenance window on a date."""
        data = body.model_dump(mode="json")
        data["created_by_user_id"] = self.user_context.user_id
        return await self._insert_inventory(
            project_id=project_id,
            facility_id=facility_id,
            table="facility_maintenance_windows",
            data=data,
        )

    async def delete_inventory(
        self, *, project_id: str, facility_id: str, table: str, row_id: str
    ) -> None:
        """Delete an inventory row by table and id."""
        await self._require_config(project_id=project_id, facility_id=facility_id)
        deleted = await self.inventory_repo.delete(
            table,
            organization_id=self._org_id,
            facility_id=facility_id,
            row_id=row_id,
        )
        if not deleted:
            raise NotFoundException(
                message_key=_TABLE_NOT_FOUND[table],
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def get_settings(self, *, project_id: str) -> dict[str, Any]:
        """Return project-wide facility booking settings."""
        await self._ensure_project(project_id)
        row = await self.config_repo.get_settings(
            organization_id=self._org_id, project_id=project_id
        )
        timezone_name = (
            str(row["timezone"])
            if row and row.get("timezone")
            else await self.timezone_for(project_id)
        )
        return {
            "project_id": project_id,
            "timezone": timezone_name,
            "currency_code": str(row["currency_code"]) if row else "INR",
            "invoice_frequency": str(row.get("invoice_frequency") or "monthly")
            if row
            else "monthly",
            "wallet_enabled": bool(row["wallet_enabled"])
            if row and "wallet_enabled" in row
            else True,
            "online_enabled": bool(row["online_enabled"])
            if row and "online_enabled" in row
            else True,
            "cash_enabled": bool(row["cash_enabled"]) if row and "cash_enabled" in row else True,
            "wallet_credit_limit": int(row.get("wallet_credit_limit") or 10000) if row else 10000,
            "updated_at": row.get("updated_at") if row else None,
        }

    async def update_settings(
        self, *, project_id: str, body: UpdateProjectBookingSettingsRequest
    ) -> dict[str, Any]:
        """Update project-wide facility booking settings."""
        await self._ensure_project(project_id)
        row = await self.config_repo.upsert_settings(
            organization_id=self._org_id,
            project_id=project_id,
            timezone=body.timezone,
            currency_code=body.currency_code,
            user_id=self.user_context.user_id,
            invoice_frequency=body.invoice_frequency.value if body.invoice_frequency else None,
            wallet_enabled=body.wallet_enabled,
            online_enabled=body.online_enabled,
            cash_enabled=body.cash_enabled,
            wallet_credit_limit=body.wallet_credit_limit,
        )
        return {
            "project_id": project_id,
            "timezone": row["timezone"],
            "currency_code": row["currency_code"],
            "invoice_frequency": str(row["invoice_frequency"]),
            "wallet_enabled": bool(row["wallet_enabled"]),
            "online_enabled": bool(row["online_enabled"]),
            "cash_enabled": bool(row["cash_enabled"]),
            "wallet_credit_limit": int(row["wallet_credit_limit"]),
            "updated_at": row.get("updated_at"),
        }

    async def list_staff_assignments(self, *, project_id: str) -> list[dict[str, Any]]:
        """List staff members assigned to operate bookable facilities."""
        await self._ensure_project(project_id)
        return await self.staff_repo.list_assignments(
            organization_id=self._org_id, project_id=project_id
        )

    async def upsert_staff_assignment(
        self, *, project_id: str, body: UpsertStaffAssignmentRequest
    ) -> dict[str, Any]:
        """Assign or replace facility operate permissions for a staff member."""
        await self._ensure_project(project_id)
        member = await self.staff_repo.get_member(
            organization_id=self._org_id,
            project_id=project_id,
            project_member_id=body.project_member_id,
        )
        if not member:
            raise NotFoundException(
                message_key="facility_booking.errors.project_member_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        for facility_id in body.facility_ids:
            config = await self.config_repo.get_config(
                organization_id=self._org_id,
                project_id=project_id,
                facility_id=facility_id,
            )
            if not config:
                raise NotFoundException(
                    message_key="facility_booking.errors.config_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
        assignment_id = await self.staff_repo.upsert_assignment(
            organization_id=self._org_id,
            project_id=project_id,
            project_member_id=body.project_member_id,
            facility_ids=body.facility_ids,
            user_id=self.user_context.user_id,
        )
        rows = await self.list_staff_assignments(project_id=project_id)
        for row in rows:
            if str(row["id"]) == assignment_id:
                return row
        return {"id": assignment_id, "project_member_id": body.project_member_id}

    async def delete_staff_assignment(self, *, project_id: str, assignment_id: str) -> None:
        """Remove a staff facility assignment."""
        await self._ensure_project(project_id)
        deleted = await self.staff_repo.delete_assignment(
            organization_id=self._org_id,
            project_id=project_id,
            assignment_id=assignment_id,
        )
        if not deleted:
            raise NotFoundException(
                message_key="facility_booking.errors.assignment_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
