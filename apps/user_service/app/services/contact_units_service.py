"""Contact-units business logic for contact onboarding."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    ContactOnboardingRepository,
)
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.schemas.contact_onboarding import AdminAssignUnitRequest
from apps.user_service.app.schemas.enums import ContactOnboardingStep, ContactUnitStatus
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class ContactUnitsService:
    """Operations on contact_units."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = ContactUnitsRepository(db_connection)
        self.onboarding_repo = ContactOnboardingRepository(db_connection)

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
        }

    async def list_my_properties(self, *, contact_id: str) -> list[dict[str, Any]]:
        """List pending and active units assigned to the contact."""
        org_id = self.user_context.organization_id
        assert org_id
        rows = await self.repo.list_by_contact(
            organization_id=org_id,
            contact_id=contact_id,
            statuses=[ContactUnitStatus.PENDING.value, ContactUnitStatus.ACTIVE.value],
        )
        return [self._normalize_unit_row(row) for row in rows]

    async def confirm_properties(
        self,
        *,
        contact_id: str,
        contact_unit_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Confirm selected pending units and complete step 1."""
        org_id = self.user_context.organization_id
        assert org_id
        updated = await self.repo.confirm_selection(
            organization_id=org_id,
            contact_id=contact_id,
            contact_unit_ids=contact_unit_ids,
        )
        if len(updated) != len(contact_unit_ids):
            raise ValidationException(
                message_key="contact_onboarding.errors.contact_unit_not_found",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self.onboarding_repo.complete_step(
            organization_id=org_id,
            contact_id=contact_id,
            step_key=ContactOnboardingStep.SELECT_PROPERTIES.value,
        )
        return [{"id": row["id"], "status": row["status"]} for row in updated]

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
        row = await self.repo.insert_allotment(
            organization_id=org_id,
            project_id=unit["project_id"],
            unit_id=body.unit_id,
            contact_id=contact_id,
            is_primary=body.is_primary,
            relationship=body.relationship.value,
        )
        return row
