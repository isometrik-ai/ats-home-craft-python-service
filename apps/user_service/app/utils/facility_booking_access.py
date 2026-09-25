"""Auth helpers for facility booking staff and resident routes."""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import Request

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.facility_staff_assignments_repository import (
    FacilityStaffAssignmentsRepository,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    ensure_staff_project_access,
    extract_onboarding_contact_context,
)
from libs.shared_utils.common_query import FACILITY_BOOKING_MANAGEMENT_CONFIGURE
from libs.shared_utils.http_exceptions import ForbiddenException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


async def ensure_booking_staff(
    *,
    current_user: dict,
    db_connection: asyncpg.Connection,
    project_id: str,
    permission_codes: str | list[str],
    request: Request | None = None,
) -> UserContext:
    """Require a project member with the given booking permission."""
    return await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=permission_codes,
        request=request,
    )


async def ensure_resident_booking_access(
    *,
    current_user: dict,
    db_connection: asyncpg.Connection,
    project_id: str,
    request: Request | None = None,
) -> tuple[UserContext, dict[str, Any]]:
    """Require an active resident contact with a unit in the project."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    has_unit = await ContactUnitsRepository(db_connection).contact_has_active_project_membership(
        organization_id=user_context.organization_id,
        contact_id=str(contact["id"]),
        project_id=project_id,
    )
    if not has_unit:
        raise ForbiddenException(
            message_key="facility_booking.errors.project_not_accessible",
            custom_code=CustomStatusCode.FORBIDDEN,
        )
    return user_context, contact


async def ensure_can_operate_facility(
    *,
    current_user: dict,
    db_connection: asyncpg.Connection,
    user_context: UserContext,
    project_id: str,
    facility_id: str,
    request: Request | None = None,
) -> None:
    """Restrict operate actions to assigned facilities unless the staff can configure."""
    try:
        await ensure_staff_project_access(
            current_user=current_user,
            db_connection=db_connection,
            project_id=project_id,
            permission_codes=FACILITY_BOOKING_MANAGEMENT_CONFIGURE,
            request=request,
        )
        return
    except ForbiddenException:
        pass
    assigned = await FacilityStaffAssignmentsRepository(db_connection).facility_ids_for_user(
        organization_id=user_context.organization_id,
        project_id=project_id,
        user_id=user_context.user_id,
    )
    if assigned is None or facility_id not in assigned:
        raise ForbiddenException(
            message_key="facility_booking.errors.facility_not_assigned",
            custom_code=CustomStatusCode.FORBIDDEN,
        )


async def ensure_host_unit(
    *,
    db_connection: asyncpg.Connection,
    organization_id: str,
    contact_id: str,
    project_id: str,
    host_unit_id: str | None,
) -> None:
    """When a host unit is supplied, it must belong to the contact in this project."""
    if not host_unit_id:
        return
    has_unit = await ContactUnitsRepository(db_connection).contact_has_active_unit(
        organization_id=organization_id,
        contact_id=contact_id,
        unit_id=host_unit_id,
    )
    if not has_unit:
        raise ValidationException(
            message_key="facility_booking.errors.host_unit_not_accessible",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    unit = await ContactUnitsRepository(db_connection).get_unit_project(
        organization_id=organization_id,
        unit_id=host_unit_id,
    )
    if not unit or str(unit.get("project_id")) != project_id:
        raise ValidationException(
            message_key="facility_booking.errors.host_unit_not_accessible",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
