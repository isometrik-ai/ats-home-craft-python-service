"""Contact-units business logic for contact onboarding."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg
from asyncpg.exceptions import UniqueViolationError

from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    ContactOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_roles_repository import (
    ContactRolesRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.schemas.contact_onboarding import AdminAssignUnitRequest
from apps.user_service.app.schemas.enums import (
    ContactOnboardingStep,
    ContactType,
    ContactUnitRelationship,
    ContactUnitStatus,
)
from apps.user_service.app.services.vehicles_service import VehiclesService
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class ContactUnitsService:
    """Operations on contact_units."""

    @staticmethod
    def _validate_contact_unit_ids(contact_unit_ids: list[str]) -> list[str]:
        """Normalize contact_unit ids and reject malformed UUIDs before DB calls."""
        normalized: list[str] = []
        for raw in contact_unit_ids:
            try:
                normalized.append(str(UUID(str(raw).strip())))
            except ValueError as exc:
                raise ValidationException(
                    message_key="contact_onboarding.errors.invalid_contact_unit_id",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                ) from exc
        return normalized

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = ContactUnitsRepository(db_connection)
        self.units_repo = UnitsRepository(db_connection)
        self.onboarding_repo = ContactOnboardingRepository(db_connection)
        self.contact_roles_repo = ContactRolesRepository(db_connection)

    @staticmethod
    def _format_assign_date(value: Any) -> str | None:
        """Format assigned_at to an ISO date string for API responses."""
        if not value:
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str):
            return value[:10]
        return str(value)[:10]

    @staticmethod
    def _assign_date_to_utc_datetime(assign_date: date) -> datetime:
        """Convert an admin assign date to UTC midnight."""
        return datetime.combine(assign_date, datetime.min.time(), tzinfo=timezone.utc)

    @staticmethod
    def _build_project_summary(row: dict[str, Any]) -> dict[str, Any] | None:
        """Map joined project columns to a nested project object."""
        project_id = row.get("project_id")
        if not project_id:
            return None
        property_types = row.get("project_property_types") or []
        if not isinstance(property_types, list):
            property_types = list(property_types)
        latitude = row.get("project_latitude")
        longitude = row.get("project_longitude")
        return {
            "id": str(project_id),
            "code": row.get("project_code") or "",
            "name": row.get("project_name") or "",
            "developer_name": row.get("project_developer_name") or "",
            "city": row.get("project_city") or "",
            "state": row.get("project_state") or "",
            "country": row.get("project_country") or "",
            "address_line_1": row.get("project_address_line_1") or "",
            "address_line_2": row.get("project_address_line_2"),
            "pin_code": row.get("project_pin_code") or "",
            "latitude": float(latitude) if latitude is not None else None,
            "longitude": float(longitude) if longitude is not None else None,
            "property_types": [str(item) for item in property_types],
        }

    def _normalize_unit_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a contact_units row to API response shape."""
        return {
            "id": str(row["id"]),
            "unit_id": str(row["unit_id"]),
            "project_id": str(row["project_id"]),
            "contact_id": str(row["contact_id"]),
            "code": row.get("code") or "",
            "unit_label": row.get("unit_label"),
            "tower_name": row.get("tower_name"),
            "floor_name": row.get("floor_name"),
            "config_label": row.get("config_label"),
            "status": row.get("status"),
            "is_primary": bool(row.get("is_primary")),
            "is_default_login": bool(row.get("is_default_login")),
            "relationship": row.get("relationship") or "self",
            "contact_type": row.get("contact_type"),
            "first_name": row.get("first_name"),
            "last_name": row.get("last_name"),
            "assign_date": self._format_assign_date(row.get("assigned_at")),
            "created_at": format_iso_datetime(row.get("created_at")),
            "parking_entitlement": int(row.get("parking_entitlement") or 0),
            "project": self._build_project_summary(row),
        }

    async def list_contact_units(
        self,
        *,
        contact_id: str,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List unit assignments for a contact (admin: all statuses by default)."""
        org_id = self.user_context.organization_id
        assert org_id
        rows = await self.repo.list_by_contact(
            organization_id=org_id,
            contact_id=contact_id,
            statuses=statuses,
        )
        return [self._normalize_unit_row(row) for row in rows]

    async def list_my_properties(self, *, contact_id: str) -> list[dict[str, Any]]:
        """List pending and active units assigned to the contact."""
        return await self.list_contact_units(
            contact_id=contact_id,
            statuses=[ContactUnitStatus.PENDING.value, ContactUnitStatus.ACTIVE.value],
        )

    @staticmethod
    def group_properties_by_project(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Group flat property rows by project, preserving first-seen project order."""
        groups: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for unit in units:
            project_id = str(unit.get("project_id") or "")
            if not project_id:
                continue
            if project_id not in groups:
                order.append(project_id)
                project = unit.get("project") or {"id": project_id}
                groups[project_id] = {
                    "project": project,
                    "units": [],
                }
            unit_payload = {key: value for key, value in unit.items() if key != "project"}
            groups[project_id]["units"].append(unit_payload)
        return [groups[project_id] for project_id in order]

    async def list_my_properties_grouped(self, *, contact_id: str) -> list[dict[str, Any]]:
        """List pending and active units grouped by project."""
        units = await self.list_my_properties(contact_id=contact_id)
        return self.group_properties_by_project(units)

    async def _confirm_pending_units(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Activate selected pending units or raise when any id is invalid."""
        contact_unit_ids = self._validate_contact_unit_ids(contact_unit_ids)
        conflicts = await self.repo.find_active_primary_conflicts(
            organization_id=organization_id,
            contact_id=contact_id,
            contact_unit_ids=contact_unit_ids,
        )
        if conflicts:
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_primary_already_assigned",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        try:
            updated = await self.repo.confirm_selection(
                organization_id=organization_id,
                contact_id=contact_id,
                contact_unit_ids=contact_unit_ids,
            )
        except UniqueViolationError as exc:
            if exc.constraint_name == "uq_contact_units_primary_per_unit":
                raise ValidationException(
                    message_key="contact_onboarding.errors.unit_primary_already_assigned",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                ) from exc
            raise
        if len(updated) != len(contact_unit_ids):
            raise ValidationException(
                message_key="contact_onboarding.errors.contact_unit_not_found",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return updated

    @staticmethod
    def _confirmed_items(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Map confirmed contact_unit rows to API items."""
        return [{"id": row["id"], "status": row["status"]} for row in rows]

    async def _accept_pending_units(
        self,
        *,
        contact_id: str,
        contact_unit_ids: list[str],
        default_contact_unit_id: str | None = None,
    ) -> dict[str, Any]:
        """Activate pending units immediately and return acceptance summary."""
        org_id = self.user_context.organization_id
        assert org_id
        updated = await self._confirm_pending_units(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_ids=contact_unit_ids,
        )
        confirmed_ids = [str(row["id"]) for row in updated]

        if len(updated) == 1:
            await self.repo.set_default_login(
                organization_id=org_id,
                contact_id=contact_id,
                contact_unit_id=str(updated[0]["id"]),
            )
        elif default_contact_unit_id:
            default_contact_unit_id = self._validate_contact_unit_ids([default_contact_unit_id])[0]
            if default_contact_unit_id not in confirmed_ids:
                raise ValidationException(
                    message_key="contact_onboarding.errors.contact_unit_not_found",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            await self.repo.set_default_login(
                organization_id=org_id,
                contact_id=contact_id,
                contact_unit_id=default_contact_unit_id,
            )

        await self.repo.activate_units_by_ids(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_ids=confirmed_ids,
        )

        active_count = await self.repo.count_active_units(
            organization_id=org_id,
            contact_id=contact_id,
        )
        has_default = await self.repo.has_default_login(
            organization_id=org_id,
            contact_id=contact_id,
        )
        return {
            "items": self._confirmed_items(updated),
            "requires_default_unit": active_count > 1 and not has_default,
        }

    async def confirm_properties(
        self,
        *,
        contact_id: str,
        contact_unit_ids: list[str],
        default_contact_unit_id: str | None = None,
    ) -> dict[str, Any]:
        """Accept selected pending units (same behavior as claim; no wizard gates)."""
        org_id = self.user_context.organization_id
        assert org_id
        result = await self._accept_pending_units(
            contact_id=contact_id,
            contact_unit_ids=contact_unit_ids,
            default_contact_unit_id=default_contact_unit_id,
        )
        await self.onboarding_repo.complete_step(
            organization_id=org_id,
            contact_id=contact_id,
            step_key=ContactOnboardingStep.SELECT_PROPERTIES.value,
        )
        if not result["requires_default_unit"]:
            await self.onboarding_repo.complete_step(
                organization_id=org_id,
                contact_id=contact_id,
                step_key=ContactOnboardingStep.CHOOSE_UNIT.value,
            )
        return result

    async def claim_properties(
        self,
        *,
        contact_id: str,
        contact_unit_ids: list[str],
    ) -> dict[str, Any]:
        """Accept pending units (alias of confirm; no onboarding completion required)."""
        return await self._accept_pending_units(
            contact_id=contact_id,
            contact_unit_ids=contact_unit_ids,
        )

    async def set_default_unit(self, *, contact_id: str, contact_unit_id: str) -> dict[str, Any]:
        """Set the default login unit and complete the choose-unit step."""
        org_id = self.user_context.organization_id
        assert org_id
        contact_unit_id = self._validate_contact_unit_ids([contact_unit_id])[0]
        row = await self.repo.set_default_login(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_id=contact_unit_id,
        )
        if not row:
            raise NotFoundException(
                message_key="contact_onboarding.errors.contact_unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self.onboarding_repo.complete_step(
            organization_id=org_id,
            contact_id=contact_id,
            step_key=ContactOnboardingStep.CHOOSE_UNIT.value,
        )
        return row

    async def _ensure_project_unit(
        self,
        *,
        project_id: str,
        unit_id: str,
    ) -> dict[str, Any]:
        """Return the unit row when it belongs to the project."""
        org_id = self.user_context.organization_id
        assert org_id
        unit = await self.repo.get_unit_project(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if not unit or str(unit["project_id"]) != project_id:
            raise NotFoundException(
                message_key="project_setup.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return unit

    async def _create_unit_allotment(
        self,
        *,
        unit_id: str,
        contact_id: str,
        is_primary: bool,
        relationship: str,
        project_id: str | None = None,
        assign_date: date | None = None,
    ) -> dict[str, Any]:
        """Assign or re-open a pending unit allotment and mark the unit occupied."""
        org_id = self.user_context.organization_id
        assert org_id
        unit = await self.repo.get_unit_project(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if not unit:
            raise NotFoundException(
                message_key="contact_onboarding.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if project_id and str(unit["project_id"]) != project_id:
            raise NotFoundException(
                message_key="project_setup.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        if not await self.repo.contact_exists(
            organization_id=org_id,
            contact_id=contact_id,
        ):
            raise NotFoundException(
                message_key="contact_onboarding.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        existing = await self.repo.get_by_unit_and_contact(
            organization_id=org_id,
            unit_id=unit_id,
            contact_id=contact_id,
        )
        if existing and existing.get("status") in {
            ContactUnitStatus.PENDING.value,
            ContactUnitStatus.ACTIVE.value,
        }:
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_already_assigned_to_contact",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if await self.repo.unit_has_primary_occupant(
            organization_id=org_id,
            unit_id=unit_id,
            exclude_contact_id=contact_id,
        ):
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_already_assigned",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        assigned_at = (
            self._assign_date_to_utc_datetime(assign_date) if assign_date is not None else None
        )

        if existing and existing.get("status") == ContactUnitStatus.MOVED_OUT.value:
            row = await self.repo.reactivate_allotment(
                organization_id=org_id,
                contact_unit_id=str(existing["id"]),
                is_primary=is_primary,
                relationship=relationship,
                assigned_at=assigned_at,
            )
            if not row:
                raise ValidationException(
                    message_key="contact_onboarding.errors.contact_unit_not_found",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
        else:
            row = await self.repo.insert_allotment(
                organization_id=org_id,
                project_id=unit["project_id"],
                unit_id=unit_id,
                contact_id=contact_id,
                is_primary=is_primary,
                relationship=relationship,
                assigned_at=assigned_at,
            )

        if relationship == ContactUnitRelationship.SELF.value:
            await self.contact_roles_repo.end_active_roles_for_unit(
                organization_id=org_id,
                unit_id=unit_id,
                role_types=[ContactType.OWNER.value, ContactType.TENANT.value],
            )
            await self.contact_roles_repo.insert_owner_role(
                organization_id=org_id,
                contact_id=contact_id,
                project_id=str(unit["project_id"]),
                unit_id=unit_id,
                contact_unit_id=str(row["id"]),
            )
        await self.units_repo.reconcile_unit_inventory_status(
            organization_id=org_id,
            project_id=str(unit["project_id"]),
            unit_id=unit_id,
        )
        return row

    async def unassign_unit_owner(
        self,
        *,
        project_id: str,
        unit_id: str,
    ) -> dict[str, Any]:
        """Remove the current unit allotment and mark the unit vacant."""
        org_id = self.user_context.organization_id
        assert org_id
        unit = await self._ensure_project_unit(project_id=project_id, unit_id=unit_id)

        async with self.db_connection.transaction():
            released = await self.repo.release_unit_owner_links(
                organization_id=org_id,
                unit_id=unit_id,
            )
            if not released:
                raise NotFoundException(
                    message_key="project_setup.errors.unit_owner_not_assigned",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
            vehicles_service = VehiclesService(
                db_connection=self.db_connection,
                user_context=self.user_context,
            )
            await vehicles_service.release_for_move_out(unit_id=unit_id)
            await self.contact_roles_repo.end_active_roles_for_unit(
                organization_id=org_id,
                unit_id=unit_id,
                role_types=[ContactType.OWNER.value, ContactType.TENANT.value],
            )

            await self.units_repo.reconcile_unit_inventory_status(
                organization_id=org_id,
                project_id=str(unit["project_id"]),
                unit_id=unit_id,
            )
        previous = released[0]
        return {
            "released_contact_unit_ids": [row["id"] for row in released],
            "previous_contact_id": previous.get("contact_id"),
            "unit_status": "vacant",
        }

    async def reassign_unit_owner(
        self,
        *,
        project_id: str,
        unit_id: str,
        contact_id: str,
        assign_date: date,
        is_primary: bool = True,
        relationship: str = ContactUnitRelationship.SELF.value,
    ) -> dict[str, Any]:
        """Replace the current unit assignee with a new contact."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._ensure_project_unit(project_id=project_id, unit_id=unit_id)

        async with self.db_connection.transaction():
            released = await self.repo.release_unit_owner_links(
                organization_id=org_id,
                unit_id=unit_id,
            )
            if released:
                vehicles_service = VehiclesService(
                    db_connection=self.db_connection,
                    user_context=self.user_context,
                )
                await vehicles_service.release_for_move_out(unit_id=unit_id)
            row = await self._create_unit_allotment(
                project_id=project_id,
                unit_id=unit_id,
                contact_id=contact_id,
                is_primary=is_primary,
                relationship=relationship,
                assign_date=assign_date,
            )
        full = await self.repo.get_by_id(
            organization_id=org_id,
            contact_unit_id=str(row["id"]),
        )
        normalized = self._normalize_unit_row(full or row)
        unit_status = (
            "occupied"
            if await self.units_repo.has_active_owner(
                organization_id=org_id,
                unit_id=unit_id,
            )
            else "vacant"
        )
        return {
            "id": row["id"],
            "status": row["status"],
            "contact_id": contact_id,
            "previous_contact_id": released[0]["contact_id"] if released else None,
            "released_contact_unit_ids": [item["id"] for item in released],
            "unit_status": unit_status,
            "assign_date": normalized.get("assign_date"),
        }

    async def admin_assign_unit(
        self,
        *,
        contact_id: str,
        body: AdminAssignUnitRequest,
    ) -> dict[str, Any]:
        """Admin pre-allotment: link a unit to a contact as pending."""
        org_id = self.user_context.organization_id
        assert org_id
        row = await self._create_unit_allotment(
            unit_id=body.unit_id,
            contact_id=contact_id,
            is_primary=body.is_primary,
            relationship=body.relationship.value,
            assign_date=body.assign_date,
        )
        full = await self.repo.get_by_id(
            organization_id=org_id,
            contact_unit_id=str(row["id"]),
        )
        return self._normalize_unit_row(full or row)
