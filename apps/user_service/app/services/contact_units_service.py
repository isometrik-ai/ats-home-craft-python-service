"""Contact-units business logic for contact onboarding."""

from __future__ import annotations

from typing import Any

import asyncpg
from asyncpg.exceptions import UniqueViolationError

from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    ContactOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_unit_onboarding_repository import (
    ContactUnitOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.units_repository import UnitsRepository
from apps.user_service.app.schemas.contact_onboarding import AdminAssignUnitRequest
from apps.user_service.app.schemas.enums import (
    ContactOnboardingStep,
    ContactUnitStatus,
)
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class ContactUnitsService:
    """Operations on contact_units."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = ContactUnitsRepository(db_connection)
        self.units_repo = UnitsRepository(db_connection)
        self.onboarding_repo = ContactOnboardingRepository(db_connection)
        self.unit_onboarding_repo = ContactUnitOnboardingRepository(db_connection)

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
            "created_at": format_iso_datetime(row.get("created_at")),
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

    async def _confirm_pending_units(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Activate selected pending units or raise when any id is invalid."""
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

    async def confirm_properties(
        self,
        *,
        contact_id: str,
        contact_unit_ids: list[str],
        default_contact_unit_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Confirm selected pending units and complete the properties step."""
        org_id = self.user_context.organization_id
        assert org_id
        profile_step = await self.onboarding_repo.list_steps(
            organization_id=org_id,
            contact_id=contact_id,
        )
        profile_status = next(
            (
                row.get("status")
                for row in profile_step
                if row.get("step_key") == ContactOnboardingStep.COMPLETE_PROFILE.value
            ),
            None,
        )
        if profile_status not in {"completed", "skipped"}:
            raise ValidationException(
                message_key="contact_onboarding.errors.profile_step_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        updated = await self._confirm_pending_units(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_ids=contact_unit_ids,
        )
        confirmed_ids = [str(row["id"]) for row in updated]
        await self.unit_onboarding_repo.ensure_steps_for_units(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_ids=confirmed_ids,
        )

        if len(updated) == 1:
            await self.repo.set_default_login(
                organization_id=org_id,
                contact_id=contact_id,
                contact_unit_id=str(updated[0]["id"]),
            )
            await self.onboarding_repo.complete_step(
                organization_id=org_id,
                contact_id=contact_id,
                step_key=ContactOnboardingStep.CHOOSE_UNIT.value,
            )
        elif default_contact_unit_id:
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
            await self.onboarding_repo.complete_step(
                organization_id=org_id,
                contact_id=contact_id,
                step_key=ContactOnboardingStep.CHOOSE_UNIT.value,
            )

        await self.onboarding_repo.complete_step(
            organization_id=org_id,
            contact_id=contact_id,
            step_key=ContactOnboardingStep.SELECT_PROPERTIES.value,
        )
        if await self.onboarding_repo.is_wizard_completed(
            organization_id=org_id,
            contact_id=contact_id,
        ):
            await self.repo.activate_units_by_ids(
                organization_id=org_id,
                contact_id=contact_id,
                contact_unit_ids=confirmed_ids,
            )
        return self._confirmed_items(updated)

    async def claim_properties(
        self,
        *,
        contact_id: str,
        contact_unit_ids: list[str],
    ) -> dict[str, Any]:
        """Accept pending units after onboarding is already complete."""
        org_id = self.user_context.organization_id
        assert org_id
        if not await self.onboarding_repo.is_wizard_completed(
            organization_id=org_id,
            contact_id=contact_id,
        ):
            raise ValidationException(
                message_key="contact_onboarding.errors.onboarding_not_completed_use_confirm",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        updated = await self._confirm_pending_units(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_ids=contact_unit_ids,
        )
        confirmed_ids = [str(row["id"]) for row in updated]
        await self.unit_onboarding_repo.ensure_steps_for_units(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_ids=confirmed_ids,
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

    async def set_default_unit(self, *, contact_id: str, contact_unit_id: str) -> dict[str, Any]:
        """Set the default login unit and complete the choose-unit step."""
        org_id = self.user_context.organization_id
        assert org_id
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

    async def admin_assign_unit(
        self,
        *,
        contact_id: str,
        body: AdminAssignUnitRequest,
    ) -> dict[str, Any]:
        """Admin pre-allotment: link a unit to a contact as pending."""
        org_id = self.user_context.organization_id
        assert org_id
        unit = await self.repo.get_unit_project(
            organization_id=org_id,
            unit_id=body.unit_id,
        )
        if not unit:
            raise NotFoundException(
                message_key="contact_onboarding.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        existing = await self.repo.get_by_unit_and_contact(
            organization_id=org_id,
            unit_id=body.unit_id,
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
            unit_id=body.unit_id,
            exclude_contact_id=contact_id,
        ):
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_already_assigned",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        row = await self.repo.insert_allotment(
            organization_id=org_id,
            project_id=unit["project_id"],
            unit_id=body.unit_id,
            contact_id=contact_id,
            is_primary=body.is_primary,
            relationship=body.relationship.value,
        )
        await self.units_repo.mark_unit_occupied(
            organization_id=org_id,
            project_id=str(unit["project_id"]),
            unit_id=body.unit_id,
        )
        return row
