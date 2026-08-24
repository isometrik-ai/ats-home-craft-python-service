"""Unit tests for resident daily help API route handlers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.daily_help_resident import (
    add_daily_help_household_link,
    create_daily_help_rating,
    get_daily_help_rating_summary,
    get_resident_daily_help_attendance,
    get_resident_daily_help_profile,
    get_resident_daily_help_rating,
    list_resident_daily_help_categories,
    list_resident_daily_help_household_links,
    list_resident_daily_help_profiles,
    mark_resident_daily_help_attendance_absence,
    remove_daily_help_household_link,
    search_resident_daily_help_profiles,
    set_resident_daily_help_open_to_work,
    update_daily_help_rating,
)
from apps.user_service.app.schemas.daily_help import (
    CreateDailyHelpRatingRequest,
    MarkDailyHelpAttendanceAbsenceRequest,
    RemoveDailyHelpHouseholdLinkRequest,
    ResidentDailyHelpListQuery,
    ResidentDailyHelpSearchQuery,
    SetDailyHelpOpenToWorkRequest,
    UpdateDailyHelpRatingRequest,
)
from apps.user_service.app.utils.common_utils import UserContext

PROFILE_ID = "22222222-2222-2222-2222-222222222222"
UNIT_ID = "33333333-3333-3333-3333-333333333333"
LINK_ID = "44444444-4444-4444-4444-444444444444"


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/daily-help",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


def _contact_context():
    user_context = UserContext(
        user_id="user-1",
        email="resident@example.com",
        organization_id="org-1",
    )
    return user_context, {"id": "contact-1"}


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.daily_help_resident.extract_onboarding_contact_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.daily_help_resident.DailyHelpService")
async def test_resident_daily_help_read_endpoints(mock_service_cls, mock_contact_ctx):
    mock_contact_ctx.return_value = _contact_context()
    service = mock_service_cls.return_value
    service.list_resident_categories = AsyncMock(return_value=[])
    service.list_resident_profiles = AsyncMock(return_value=([], 0))
    service.search_resident_profiles = AsyncMock(return_value=([], 0))
    service.list_resident_household_links = AsyncMock(return_value=[])
    service.get_resident_detail = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"id": PROFILE_ID})
    )
    service.get_resident_rating = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"stars": "4.0"})
    )
    service.get_rating_summary = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"average_stars": "4.0"})
    )
    service.get_attendance = AsyncMock(return_value={"days": []})

    db = MagicMock()
    user = {"sub": "user-1"}
    assert (
        await list_resident_daily_help_categories(
            request=_request(),
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await list_resident_daily_help_profiles(
            request=_request(),
            query=ResidentDailyHelpListQuery(unit_id=UNIT_ID),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await search_resident_daily_help_profiles(
            request=_request(),
            query=ResidentDailyHelpSearchQuery(unit_id=UNIT_ID, q="maid"),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await list_resident_daily_help_household_links(
            request=_request(),
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await get_resident_daily_help_profile(
            request=_request(),
            profile_id=PROFILE_ID,
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await get_resident_daily_help_rating(
            request=_request(),
            profile_id=PROFILE_ID,
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await get_daily_help_rating_summary(
            request=_request(),
            profile_id=PROFILE_ID,
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await get_resident_daily_help_attendance(
            request=_request(),
            profile_id=PROFILE_ID,
            unit_id=UNIT_ID,
            month=8,
            year=2026,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.daily_help_resident.extract_onboarding_contact_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.daily_help_resident.DailyHelpService")
async def test_resident_daily_help_write_endpoints(mock_service_cls, mock_contact_ctx):
    mock_contact_ctx.return_value = _contact_context()
    service = mock_service_cls.return_value
    service.set_resident_open_to_work = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"open_to_work": True})
    )
    service.add_household_link = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"id": "link-1"})
    )
    service.remove_household_link = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"removed": True})
    )
    service.create_rating = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"stars": "5.0"})
    )
    service.update_rating = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"stars": "4.0"})
    )
    service.mark_attendance_absence = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"marked": True})
    )

    db = MagicMock()
    user = {"sub": "user-1"}
    assert (
        await set_resident_daily_help_open_to_work(
            request=_request(),
            profile_id=PROFILE_ID,
            unit_id=UNIT_ID,
            body=SetDailyHelpOpenToWorkRequest(open_to_work=True),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await add_daily_help_household_link(
            request=_request(),
            profile_id=PROFILE_ID,
            unit_id=UNIT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 201
    assert (
        await remove_daily_help_household_link(
            request=_request(),
            profile_id=PROFILE_ID,
            link_id=LINK_ID,
            unit_id=UNIT_ID,
            body=RemoveDailyHelpHouseholdLinkRequest(reason="Moved out"),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await create_daily_help_rating(
            request=_request(),
            profile_id=PROFILE_ID,
            unit_id=UNIT_ID,
            body=CreateDailyHelpRatingRequest(stars=Decimal("5.0")),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 201
    assert (
        await update_daily_help_rating(
            request=_request(),
            profile_id=PROFILE_ID,
            unit_id=UNIT_ID,
            body=UpdateDailyHelpRatingRequest(stars=Decimal("4.0")),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await mark_resident_daily_help_attendance_absence(
            request=_request(),
            profile_id=PROFILE_ID,
            unit_id=UNIT_ID,
            body=MarkDailyHelpAttendanceAbsenceRequest(attendance_date=date(2026, 8, 1)),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.daily_help_resident.extract_onboarding_contact_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.daily_help_resident.DailyHelpService")
async def test_resident_attendance_endpoint_passes_unit_id(mock_service_cls, mock_contact_ctx):
    mock_contact_ctx.return_value = _contact_context()
    mock_service_cls.return_value.get_attendance = AsyncMock(return_value={"days": []})

    response = await get_resident_daily_help_attendance(
        request=_request(),
        profile_id=PROFILE_ID,
        unit_id=UNIT_ID,
        month=8,
        year=2026,
        db_connection=MagicMock(),
        current_user={"sub": "user-1"},
    )

    assert response.status_code == 200
    mock_service_cls.return_value.get_attendance.assert_awaited_once_with(
        contact_id="contact-1",
        unit_id=UNIT_ID,
        profile_id=PROFILE_ID,
        year=2026,
        month=8,
    )


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.daily_help_resident.extract_onboarding_contact_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.daily_help_resident.DailyHelpService")
async def test_resident_mark_absence_endpoint_passes_contact_and_unit(
    mock_service_cls, mock_contact_ctx
):
    mock_contact_ctx.return_value = _contact_context()
    mock_service_cls.return_value.mark_attendance_absence = AsyncMock(
        return_value={"date": "2026-08-24", "status": "absent"}
    )

    response = await mark_resident_daily_help_attendance_absence(
        request=_request(),
        profile_id=PROFILE_ID,
        unit_id=UNIT_ID,
        body=MarkDailyHelpAttendanceAbsenceRequest(attendance_date=date(2026, 8, 24)),
        db_connection=MagicMock(),
        current_user={"sub": "user-1"},
    )

    assert response.status_code == 200
    mock_service_cls.return_value.mark_attendance_absence.assert_awaited_once_with(
        contact_id="contact-1",
        unit_id=UNIT_ID,
        profile_id=PROFILE_ID,
        attendance_date=date(2026, 8, 24),
    )
