"""Unit tests for facility booking access helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.facility_booking_access import (
    ensure_booking_staff,
    ensure_can_operate_facility,
    ensure_host_unit,
    ensure_resident_booking_access,
)
from libs.shared_utils.http_exceptions import ForbiddenException, ValidationException

ORG_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_ID = "22222222-2222-2222-2222-222222222222"
FACILITY_ID = "33333333-3333-3333-3333-333333333333"
CONTACT_ID = "55555555-5555-5555-5555-555555555555"
UNIT_ID = "66666666-6666-6666-6666-666666666666"
USER_ID = "44444444-4444-4444-4444-444444444444"

_MODULE = "apps.user_service.app.utils.facility_booking_access"


def _user_context() -> UserContext:
    return UserContext(user_id=USER_ID, email="u@example.com", organization_id=ORG_ID)


@pytest.mark.asyncio
@patch(f"{_MODULE}.ensure_staff_project_access", new_callable=AsyncMock)
async def test_ensure_booking_staff_delegates(mock_ensure: AsyncMock) -> None:
    """Staff booking access forwards to ensure_staff_project_access."""
    expected = _user_context()
    mock_ensure.return_value = expected
    db = MagicMock()
    current_user = {"sub": USER_ID}

    ctx = await ensure_booking_staff(
        current_user=current_user,
        db_connection=db,
        project_id=PROJECT_ID,
        permission_codes="facility_booking.view",
        request=MagicMock(),
    )

    assert ctx is expected
    mock_ensure.assert_awaited_once_with(
        current_user=current_user,
        db_connection=db,
        project_id=PROJECT_ID,
        permission_codes="facility_booking.view",
        request=mock_ensure.await_args.kwargs["request"],
    )


@pytest.mark.asyncio
@patch(f"{_MODULE}.ContactUnitsRepository")
@patch(f"{_MODULE}.extract_onboarding_contact_context", new_callable=AsyncMock)
async def test_ensure_resident_booking_access_success(
    mock_extract: AsyncMock, mock_units_cls: MagicMock
) -> None:
    """Resident with active project membership receives context and contact."""
    user_context = _user_context()
    contact = {"id": CONTACT_ID}
    mock_extract.return_value = (user_context, contact)
    units_repo = MagicMock()
    units_repo.contact_has_active_project_membership = AsyncMock(return_value=True)
    mock_units_cls.return_value = units_repo
    db = MagicMock()

    ctx, out_contact = await ensure_resident_booking_access(
        current_user={"sub": USER_ID},
        db_connection=db,
        project_id=PROJECT_ID,
    )

    assert ctx is user_context
    assert out_contact is contact
    units_repo.contact_has_active_project_membership.assert_awaited_once_with(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        project_id=PROJECT_ID,
    )


@pytest.mark.asyncio
@patch(f"{_MODULE}.ContactUnitsRepository")
@patch(f"{_MODULE}.extract_onboarding_contact_context", new_callable=AsyncMock)
async def test_ensure_resident_booking_access_forbidden_without_unit(
    mock_extract: AsyncMock, mock_units_cls: MagicMock
) -> None:
    """Residents without a unit in the project are rejected."""
    mock_extract.return_value = (_user_context(), {"id": CONTACT_ID})
    units_repo = MagicMock()
    units_repo.contact_has_active_project_membership = AsyncMock(return_value=False)
    mock_units_cls.return_value = units_repo

    with pytest.raises(ForbiddenException):
        await ensure_resident_booking_access(
            current_user={"sub": USER_ID},
            db_connection=MagicMock(),
            project_id=PROJECT_ID,
        )


@pytest.mark.asyncio
@patch(f"{_MODULE}.ensure_staff_project_access", new_callable=AsyncMock)
async def test_ensure_can_operate_facility_configure_permission(
    mock_ensure: AsyncMock,
) -> None:
    """Configure permission bypasses facility assignment checks."""
    mock_ensure.return_value = _user_context()

    await ensure_can_operate_facility(
        current_user={"sub": USER_ID},
        db_connection=MagicMock(),
        user_context=_user_context(),
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
    )

    mock_ensure.assert_awaited_once()


@pytest.mark.asyncio
@patch(f"{_MODULE}.FacilityStaffAssignmentsRepository")
@patch(f"{_MODULE}.ensure_staff_project_access", new_callable=AsyncMock)
async def test_ensure_can_operate_facility_assigned_facility(
    mock_ensure: AsyncMock, mock_assign_cls: MagicMock
) -> None:
    """Staff assigned to the facility may operate without configure permission."""
    mock_ensure.side_effect = ForbiddenException(message_key="forbidden")
    assign_repo = MagicMock()
    assign_repo.facility_ids_for_user = AsyncMock(return_value=frozenset({FACILITY_ID}))
    mock_assign_cls.return_value = assign_repo

    await ensure_can_operate_facility(
        current_user={"sub": USER_ID},
        db_connection=MagicMock(),
        user_context=_user_context(),
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
    )


@pytest.mark.asyncio
@patch(f"{_MODULE}.FacilityStaffAssignmentsRepository")
@patch(f"{_MODULE}.ensure_staff_project_access", new_callable=AsyncMock)
async def test_ensure_can_operate_facility_no_assignments(
    mock_ensure: AsyncMock, mock_assign_cls: MagicMock
) -> None:
    """Missing assignment list is treated as no facility access."""
    mock_ensure.side_effect = ForbiddenException(message_key="forbidden")
    assign_repo = MagicMock()
    assign_repo.facility_ids_for_user = AsyncMock(return_value=None)
    mock_assign_cls.return_value = assign_repo

    with pytest.raises(ForbiddenException):
        await ensure_can_operate_facility(
            current_user={"sub": USER_ID},
            db_connection=MagicMock(),
            user_context=_user_context(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
        )


@pytest.mark.asyncio
@patch(f"{_MODULE}.FacilityStaffAssignmentsRepository")
@patch(f"{_MODULE}.ensure_staff_project_access", new_callable=AsyncMock)
async def test_ensure_can_operate_facility_not_assigned(
    mock_ensure: AsyncMock, mock_assign_cls: MagicMock
) -> None:
    """Staff without configure or assignment cannot operate the facility."""
    mock_ensure.side_effect = ForbiddenException(message_key="forbidden")
    assign_repo = MagicMock()
    assign_repo.facility_ids_for_user = AsyncMock(return_value=frozenset({"other-facility"}))
    mock_assign_cls.return_value = assign_repo

    with pytest.raises(ForbiddenException):
        await ensure_can_operate_facility(
            current_user={"sub": USER_ID},
            db_connection=MagicMock(),
            user_context=_user_context(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
        )


@pytest.mark.asyncio
@patch(f"{_MODULE}.ContactUnitsRepository")
async def test_ensure_host_unit_skips_when_missing(mock_units_cls: MagicMock) -> None:
    """No host unit id skips validation."""
    await ensure_host_unit(
        db_connection=MagicMock(),
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        project_id=PROJECT_ID,
        host_unit_id=None,
    )
    mock_units_cls.assert_not_called()


@pytest.mark.asyncio
@patch(f"{_MODULE}.ContactUnitsRepository")
async def test_ensure_host_unit_rejects_inaccessible_unit(mock_units_cls: MagicMock) -> None:
    """Host unit must be an active unit for the contact."""
    units_repo = MagicMock()
    units_repo.contact_has_active_unit = AsyncMock(return_value=False)
    mock_units_cls.return_value = units_repo

    with pytest.raises(ValidationException):
        await ensure_host_unit(
            db_connection=MagicMock(),
            organization_id=ORG_ID,
            contact_id=CONTACT_ID,
            project_id=PROJECT_ID,
            host_unit_id=UNIT_ID,
        )


@pytest.mark.asyncio
@patch(f"{_MODULE}.ContactUnitsRepository")
async def test_ensure_host_unit_rejects_wrong_project(mock_units_cls: MagicMock) -> None:
    """Host unit must belong to the booking project."""
    units_repo = MagicMock()
    units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    units_repo.get_unit_project = AsyncMock(return_value={"project_id": "other-project"})
    mock_units_cls.return_value = units_repo

    with pytest.raises(ValidationException):
        await ensure_host_unit(
            db_connection=MagicMock(),
            organization_id=ORG_ID,
            contact_id=CONTACT_ID,
            project_id=PROJECT_ID,
            host_unit_id=UNIT_ID,
        )


@pytest.mark.asyncio
@patch(f"{_MODULE}.ContactUnitsRepository")
async def test_ensure_host_unit_success(mock_units_cls: MagicMock) -> None:
    """Valid host unit in project passes validation."""
    units_repo = MagicMock()
    units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    units_repo.get_unit_project = AsyncMock(return_value={"project_id": PROJECT_ID})
    mock_units_cls.return_value = units_repo

    await ensure_host_unit(
        db_connection=MagicMock(),
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        project_id=PROJECT_ID,
        host_unit_id=UNIT_ID,
    )
