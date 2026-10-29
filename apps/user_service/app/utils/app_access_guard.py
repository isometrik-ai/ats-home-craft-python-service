"""Resident-only hold check. Staff callers must not use this helper."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.app_access_repository import (
    AppAccessRepository,
    as_decimal,
)
from apps.user_service.app.schemas.enums.app_access import AppAccessStatus
from apps.user_service.app.services.app_access_evaluate import reason_key_for
from apps.user_service.app.services.fee_configuration_labels import money_str
from libs.shared_utils.http_exceptions import ForbiddenException
from libs.shared_utils.status_codes import CustomStatusCode


def _raise_held(state: dict[str, Any], *, unit_id: str) -> None:
    """403 with hold details on the existing errors bag."""
    raise ForbiddenException(
        message_key="app_access.errors.unit_held",
        custom_code=CustomStatusCode.FORBIDDEN,
        errors=[
            {
                "unit_id": unit_id,
                "outstanding_amount": money_str(as_decimal(state.get("outstanding_amount"))),
                "reason_key": reason_key_for(
                    status=str(state.get("status") or ""),
                    blocked_by=state.get("blocked_by"),
                ),
            }
        ],
    )


async def ensure_resident_unit_not_held(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str,
    project_id: str,
    unit_id: str,
) -> None:
    """Raise 403 when this unit is blocked. Missing state is not held."""
    state = await AppAccessRepository(db_connection).get_state(
        organization_id=organization_id,
        project_id=project_id,
        unit_id=unit_id,
    )
    if state and state.get("status") == AppAccessStatus.BLOCKED.value:
        _raise_held(state, unit_id=unit_id)


async def ensure_resident_project_not_held(
    db_connection: asyncpg.Connection,
    *,
    organization_id: str,
    contact_id: str,
    project_id: str,
) -> None:
    """Hold-check the contact's unit in the project when the caller has no unit_id.

    Default/login unit in the project wins. One unit uses that unit. Several units
    with no default: 403 if any linked unit in the project is held.
    """
    repo = AppAccessRepository(db_connection)
    units = await repo.list_contact_units_in_project(
        organization_id=organization_id,
        contact_id=contact_id,
        project_id=project_id,
    )
    if not units:
        return
    default = next((row for row in units if row.get("is_default_login")), None)
    if default is not None:
        await ensure_resident_unit_not_held(
            db_connection,
            organization_id=organization_id,
            project_id=project_id,
            unit_id=str(default["unit_id"]),
        )
        return
    if len(units) == 1:
        await ensure_resident_unit_not_held(
            db_connection,
            organization_id=organization_id,
            project_id=project_id,
            unit_id=str(units[0]["unit_id"]),
        )
        return
    for row in units:
        state = await repo.get_state(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=str(row["unit_id"]),
        )
        if state and state.get("status") == AppAccessStatus.BLOCKED.value:
            _raise_held(state, unit_id=str(row["unit_id"]))
