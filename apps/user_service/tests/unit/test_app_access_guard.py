"""Unit tests for the resident hold helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums.app_access import AppAccessStatus
from apps.user_service.app.utils.app_access_guard import (
    ensure_resident_project_not_held,
    ensure_resident_unit_not_held,
)
from libs.shared_utils.http_exceptions import ForbiddenException


@pytest.mark.asyncio
async def test_missing_state_is_not_held(monkeypatch):
    monkeypatch.setattr(
        "apps.user_service.app.utils.app_access_guard.AppAccessRepository.get_state",
        AsyncMock(return_value=None),
    )
    await ensure_resident_unit_not_held(
        MagicMock(),
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
    )


@pytest.mark.asyncio
async def test_blocked_state_raises_403(monkeypatch):
    monkeypatch.setattr(
        "apps.user_service.app.utils.app_access_guard.AppAccessRepository.get_state",
        AsyncMock(
            return_value={
                "status": AppAccessStatus.BLOCKED.value,
                "outstanding_amount": "48200.00",
                "blocked_by": "rule",
            }
        ),
    )
    with pytest.raises(ForbiddenException) as exc:
        await ensure_resident_unit_not_held(
            MagicMock(),
            organization_id="org-1",
            project_id="project-1",
            unit_id="unit-1",
        )
    assert exc.value.message_key == "app_access.errors.unit_held"
    assert exc.value.errors[0]["unit_id"] == "unit-1"
    assert exc.value.errors[0]["reason_key"] == "app_access.hold.rule"


@pytest.mark.asyncio
async def test_project_hold_uses_default_unit(monkeypatch):
    monkeypatch.setattr(
        "apps.user_service.app.utils.app_access_guard.AppAccessRepository.list_contact_units_in_project",
        AsyncMock(
            return_value=[
                {"unit_id": "unit-a", "is_default_login": False},
                {"unit_id": "unit-b", "is_default_login": True},
            ]
        ),
    )
    seen: list[str] = []

    async def fake_unit(_db, *, organization_id, project_id, unit_id):
        del _db, organization_id, project_id
        seen.append(unit_id)

    monkeypatch.setattr(
        "apps.user_service.app.utils.app_access_guard.ensure_resident_unit_not_held",
        fake_unit,
    )
    await ensure_resident_project_not_held(
        MagicMock(),
        organization_id="org-1",
        contact_id="contact-1",
        project_id="project-1",
    )
    assert seen == ["unit-b"]
