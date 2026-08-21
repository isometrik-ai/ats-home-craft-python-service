"""Unit tests for resident community events API route handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.community_events_resident import (
    book_community_event,
    cancel_community_event_booking,
    get_my_community_event_booking,
    get_my_community_event_bookings_summary,
    get_resident_community_event,
    list_my_community_event_bookings,
    list_resident_community_events,
    verify_community_event_booking,
)
from apps.user_service.app.schemas.community_events import (
    CreateEventBookingRequest,
    ResidentEventListQuery,
    VerifyBookingRequest,
)
from apps.user_service.app.utils.common_utils import UserContext

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
EVENT_ID = "22222222-2222-2222-2222-222222222222"
BOOKING_ID = "33333333-3333-3333-3333-333333333333"


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/community-events",
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
    "apps.user_service.app.api.community_events_resident.extract_onboarding_contact_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.community_events_resident.CommunityEventsResidentService")
async def test_resident_event_read_endpoints(mock_service_cls, mock_contact_ctx):
    mock_contact_ctx.return_value = _contact_context()
    service = mock_service_cls.return_value
    service.list_events = AsyncMock(return_value=([], 0))
    service.list_my_bookings = AsyncMock(return_value=[])
    service.get_my_booking_summary = AsyncMock(
        return_value=MagicMock(model_dump=lambda: {"active": 0})
    )
    service.get_event_detail = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"id": EVENT_ID})
    )
    service.get_my_booking_for_event = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"id": BOOKING_ID})
    )

    db = MagicMock()
    user = {"sub": "user-1"}
    assert (
        await list_resident_community_events(
            request=_request(),
            project_id=PROJECT_ID,
            query=ResidentEventListQuery(),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await list_my_community_event_bookings(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await get_my_community_event_bookings_summary(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await get_resident_community_event(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await get_my_community_event_booking(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.community_events_resident.extract_onboarding_contact_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.community_events_resident.CommunityEventsResidentService")
async def test_resident_book_and_cancel(mock_service_cls, mock_contact_ctx):
    mock_contact_ctx.return_value = _contact_context()
    service = mock_service_cls.return_value
    service.book_event = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"id": BOOKING_ID})
    )
    service.cancel_booking = AsyncMock(return_value=None)

    db = MagicMock()
    user = {"sub": "user-1"}
    assert (
        await book_community_event(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            body=CreateEventBookingRequest(adult_tickets=1),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 201
    assert (
        await cancel_community_event_booking(
            request=_request(),
            project_id=PROJECT_ID,
            booking_id=BOOKING_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.community_events_resident.extract_onboarding_contact_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.community_events_resident.CommunityEventBookingService")
async def test_verify_booking_at_gate(mock_booking_cls, mock_contact_ctx):
    mock_contact_ctx.return_value = _contact_context()
    mock_booking_cls.return_value.verify_booking_at_gate = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"valid": True})
    )

    response = await verify_community_event_booking(
        request=_request(),
        project_id=PROJECT_ID,
        body=VerifyBookingRequest(gate_qr_token="token-123"),
        db_connection=MagicMock(),
        current_user={"sub": "user-1"},
    )
    assert response.status_code == 200
