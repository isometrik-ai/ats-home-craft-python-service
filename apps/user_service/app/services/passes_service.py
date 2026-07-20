"""Visitor passes business logic."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.pass_events_repository import (
    PassEventsRepository,
)
from apps.user_service.app.db.repositories.passes_repository import PassesRepository
from apps.user_service.app.schemas.enums import (
    PassActorType,
    PassDisplayStatus,
    PassEventType,
    PassStatus,
    PassValidityType,
)
from apps.user_service.app.schemas.passes import CreatePassRequest, UpdatePassRequest
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import (
    InternalServerErrorException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

_CODE_MAX_ATTEMPTS = 10


class PassesService:
    """Resident-facing visitor pass operations."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.passes_repo = PassesRepository(db_connection)
        self.events_repo = PassEventsRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)

    @staticmethod
    def _now() -> datetime:
        """Return current UTC time."""
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        """Parse a DB datetime value."""
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        return None

    @classmethod
    def derive_display_status(cls, row: dict[str, Any], *, now: datetime | None = None) -> str:
        """Derive UI display status from a pass row."""
        now = now or cls._now()
        status = str(row.get("status") or "")
        if status == PassStatus.CANCELLED.value:
            return PassDisplayStatus.CANCELLED.value
        if status == PassStatus.COMPLETED.value:
            return PassDisplayStatus.USED.value
        validity_type = str(row.get("validity_type") or "")
        entry_count = int(row.get("entry_count") or 0)
        if validity_type == PassValidityType.ONE_TIME.value and entry_count > 0:
            return PassDisplayStatus.USED.value
        valid_from = cls._parse_dt(row.get("valid_from"))
        valid_until = cls._parse_dt(row.get("valid_until"))
        if valid_until and now > valid_until:
            return PassDisplayStatus.EXPIRED.value
        if status == PassStatus.EXPIRED.value:
            return PassDisplayStatus.EXPIRED.value
        if valid_from and now < valid_from:
            return PassDisplayStatus.UPCOMING.value
        return PassDisplayStatus.ACTIVE.value

    @staticmethod
    def _random_code() -> str:
        """Generate a 4-digit numeric code."""
        return f"{secrets.randbelow(10_000):04d}"

    async def _generate_unique_code(self, *, organization_id: str) -> str:
        """Generate a 4-digit code unique among active passes in the org."""
        for _ in range(_CODE_MAX_ATTEMPTS):
            code = self._random_code()
            if not await self.passes_repo.code_exists_active(
                organization_id=organization_id,
                code=code,
            ):
                return code
        raise InternalServerErrorException(
            message_key="passes.errors.code_generation_failed",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        )

    def _normalize_event(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a pass_events row to API shape."""
        metadata = row.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            metadata = dict(metadata) if hasattr(metadata, "items") else {}
        return {
            "id": str(row["id"]),
            "event_type": row.get("event_type"),
            "gate_id": row.get("gate_id"),
            "actor_type": row.get("actor_type"),
            "actor_user_id": row.get("actor_user_id"),
            "actor_label": row.get("actor_label"),
            "occurred_at": format_iso_datetime(row.get("occurred_at")),
            "notes": row.get("notes"),
            "metadata": metadata or {},
            "entry_method": row.get("entry_method"),
            "access_status": row.get("access_status"),
        }

    def _normalize_pass(
        self,
        row: dict[str, Any],
        *,
        events: list[dict[str, Any]] | None = None,
        include_events: bool = False,
    ) -> dict[str, Any]:
        """Map a passes row to API shape."""
        display_status = self.derive_display_status(row)
        payload: dict[str, Any] = {
            "id": str(row["id"]),
            "organization_id": str(row["organization_id"]),
            "project_id": str(row["project_id"]),
            "unit_id": str(row["unit_id"]),
            "host_contact_id": str(row["host_contact_id"]),
            "pass_type": row.get("pass_type"),
            "guest_name": row.get("guest_name"),
            "guest_phone_isd_code": row.get("guest_phone_isd_code"),
            "guest_phone_number": row.get("guest_phone_number"),
            "visitor_count": int(row.get("visitor_count") or 1),
            "vehicle_number": row.get("vehicle_number"),
            "purpose": row.get("purpose"),
            "valid_from": format_iso_datetime(row.get("valid_from")),
            "valid_until": format_iso_datetime(row.get("valid_until")),
            "validity_type": row.get("validity_type"),
            "allow_multiple_entries": bool(row.get("allow_multiple_entries")),
            "is_private": bool(row.get("is_private")),
            "max_entries": row.get("max_entries"),
            "entry_count": int(row.get("entry_count") or 0),
            "status": row.get("status"),
            "display_status": display_status,
            "code": row.get("code"),
            "pass_image_path": row.get("pass_image_path"),
            "notes": row.get("notes"),
            "unit_code": row.get("unit_code"),
            "unit_label": row.get("unit_label"),
            "tower_name": row.get("tower_name"),
            "floor_name": row.get("floor_name"),
            "config_label": row.get("config_label"),
            "created_at": format_iso_datetime(row.get("created_at")),
            "updated_at": format_iso_datetime(row.get("updated_at")),
        }
        if include_events:
            payload["events"] = events or []
        return payload

    def _normalize_list_item(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a pass row to list item shape."""
        return {
            "id": str(row["id"]),
            "code": row.get("code"),
            "guest_name": row.get("guest_name"),
            "pass_type": row.get("pass_type"),
            "unit_id": str(row["unit_id"]),
            "unit_label": row.get("unit_label"),
            "tower_name": row.get("tower_name"),
            "valid_from": format_iso_datetime(row.get("valid_from")),
            "valid_until": format_iso_datetime(row.get("valid_until")),
            "validity_type": row.get("validity_type"),
            "status": row.get("status"),
            "display_status": self.derive_display_status(row),
            "entry_count": int(row.get("entry_count") or 0),
            "is_private": bool(row.get("is_private")),
        }

    async def _assert_unit_owned(self, *, contact_id: str, unit_id: str) -> dict[str, Any]:
        """Ensure the contact actively owns the unit; return unit project row."""
        org_id = self.user_context.organization_id
        assert org_id
        has_unit = await self.contact_units_repo.contact_has_active_unit(
            organization_id=org_id,
            contact_id=contact_id,
            unit_id=unit_id,
        )
        if not has_unit:
            raise ValidationException(
                message_key="passes.errors.unit_not_owned",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        unit = await self.contact_units_repo.get_unit_project(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if not unit:
            raise NotFoundException(
                message_key="passes.errors.unit_not_owned",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return unit

    async def _get_owned_pass_row(self, *, contact_id: str, pass_id: str) -> dict[str, Any]:
        """Load a pass owned by the contact or raise 404."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self.passes_repo.get_owned_by_contact(
            organization_id=org_id,
            host_contact_id=contact_id,
            pass_id=pass_id,
        )
        if not row:
            raise NotFoundException(
                message_key="passes.errors.pass_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return row

    def _assert_editable(self, row: dict[str, Any]) -> None:
        """Reject edits/cancels on non-editable passes."""
        display_status = self.derive_display_status(row)
        if display_status not in {
            PassDisplayStatus.UPCOMING.value,
            PassDisplayStatus.ACTIVE.value,
        }:
            raise ValidationException(
                message_key="passes.errors.pass_not_editable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    @staticmethod
    def _validate_validity_window(valid_from: datetime, valid_until: datetime) -> None:
        """Ensure valid_until is after valid_from."""
        if valid_until <= valid_from:
            raise ValidationException(
                message_key="passes.errors.invalid_validity",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    async def _record_event(
        self,
        *,
        pass_id: str,
        event_type: str,
        actor_type: str,
        notes: str | None = None,
    ) -> None:
        """Append a resident-scoped pass event."""
        org_id = self.user_context.organization_id
        assert org_id
        await self.events_repo.insert_event(
            {
                "organization_id": org_id,
                "pass_id": pass_id,
                "event_type": event_type,
                "actor_type": actor_type,
                "actor_user_id": self.user_context.user_id,
                "notes": notes,
            }
        )

    async def create_pass(self, *, contact_id: str, body: CreatePassRequest) -> dict[str, Any]:
        """Create a visitor pass for a guest."""
        org_id = self.user_context.organization_id
        assert org_id
        self._validate_validity_window(body.valid_from, body.valid_until)
        unit = await self._assert_unit_owned(contact_id=contact_id, unit_id=body.unit_id)
        code = await self._generate_unique_code(organization_id=org_id)

        inserted = await self.passes_repo.insert(
            {
                "organization_id": org_id,
                "project_id": unit["project_id"],
                "unit_id": body.unit_id,
                "host_contact_id": contact_id,
                "pass_type": body.pass_type.value,
                "guest_name": body.guest_name,
                "guest_phone_isd_code": body.guest_phone_isd_code,
                "guest_phone_number": body.guest_phone_number,
                "visitor_count": body.visitor_count,
                "vehicle_number": body.vehicle_number,
                "purpose": body.purpose,
                "valid_from": body.valid_from,
                "valid_until": body.valid_until,
                "validity_type": body.validity_type.value,
                "allow_multiple_entries": body.allow_multiple_entries,
                "is_private": body.is_private,
                "max_entries": body.max_entries,
                "notes": body.notes,
                "code": code,
                "created_by_contact_id": contact_id,
            }
        )
        pass_id = str(inserted["id"])
        await self._record_event(
            pass_id=pass_id,
            event_type=PassEventType.CREATED.value,
            actor_type=PassActorType.RESIDENT.value,
        )
        row = await self._get_owned_pass_row(contact_id=contact_id, pass_id=pass_id)
        return self._normalize_pass(row)

    async def list_passes(
        self,
        *,
        contact_id: str,
        bucket: str | None = None,
        display_status: str | None = None,
        unit_id: str | None = None,
        pass_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """List passes for the current host."""
        org_id = self.user_context.organization_id
        assert org_id
        rows, total = await self.passes_repo.list_by_contact(
            organization_id=org_id,
            host_contact_id=contact_id,
            bucket=bucket,
            display_status=display_status,
            unit_id=unit_id,
            pass_type=pass_type,
            page=page,
            page_size=page_size,
        )
        return [self._normalize_list_item(row) for row in rows], total

    async def get_pass(
        self,
        *,
        contact_id: str,
        pass_id: str,
        include_events: bool = True,
    ) -> dict[str, Any]:
        """Return pass details for the host."""
        row = await self._get_owned_pass_row(contact_id=contact_id, pass_id=pass_id)
        events: list[dict[str, Any]] | None = None
        if include_events:
            org_id = self.user_context.organization_id
            assert org_id
            event_rows = await self.events_repo.list_by_pass(
                organization_id=org_id,
                pass_id=pass_id,
            )
            events = [self._normalize_event(event_row) for event_row in event_rows]
        return self._normalize_pass(row, events=events, include_events=include_events)

    async def update_pass(
        self,
        *,
        contact_id: str,
        pass_id: str,
        body: UpdatePassRequest,
    ) -> dict[str, Any]:
        """Update an upcoming or active pass."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self._get_owned_pass_row(contact_id=contact_id, pass_id=pass_id)
        self._assert_editable(row)

        update_data = body.model_dump(exclude_unset=True)
        if not update_data:
            return self._normalize_pass(row)

        valid_from = update_data.get("valid_from", self._parse_dt(row.get("valid_from")))
        valid_until = update_data.get("valid_until", self._parse_dt(row.get("valid_until")))
        if valid_from and valid_until:
            self._validate_validity_window(valid_from, valid_until)

        if "pass_type" in update_data and update_data["pass_type"] is not None:
            update_data["pass_type"] = update_data["pass_type"].value
        if "validity_type" in update_data and update_data["validity_type"] is not None:
            update_data["validity_type"] = update_data["validity_type"].value

        updated = await self.passes_repo.update(
            organization_id=org_id,
            host_contact_id=contact_id,
            pass_id=pass_id,
            update_data=update_data,
        )
        if not updated:
            raise NotFoundException(
                message_key="passes.errors.pass_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._normalize_pass(updated)

    async def cancel_pass(self, *, contact_id: str, pass_id: str) -> dict[str, Any]:
        """Cancel an upcoming or active pass."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self._get_owned_pass_row(contact_id=contact_id, pass_id=pass_id)
        self._assert_editable(row)

        cancelled = await self.passes_repo.cancel(
            organization_id=org_id,
            host_contact_id=contact_id,
            pass_id=pass_id,
        )
        if not cancelled:
            raise ValidationException(
                message_key="passes.errors.pass_not_editable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self._record_event(
            pass_id=pass_id,
            event_type=PassEventType.CANCELLED.value,
            actor_type=PassActorType.RESIDENT.value,
        )
        updated = await self._get_owned_pass_row(contact_id=contact_id, pass_id=pass_id)
        return self._normalize_pass(updated)

    async def list_events(self, *, contact_id: str, pass_id: str) -> list[dict[str, Any]]:
        """Return timeline events for a pass owned by the host."""
        await self._get_owned_pass_row(contact_id=contact_id, pass_id=pass_id)
        org_id = self.user_context.organization_id
        assert org_id
        rows = await self.events_repo.list_by_pass(
            organization_id=org_id,
            pass_id=pass_id,
        )
        return [self._normalize_event(row) for row in rows]
