"""Unit tests for community events admin API route handlers."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.community_events import (
    cancel_community_event,
    complete_community_event,
    create_community_event,
    delete_community_event,
    export_community_event_bookings,
    export_community_events,
    get_community_event,
    get_community_events_summary,
    list_community_event_bookings,
    list_community_events,
    mark_community_event_booking_paid,
    mark_community_event_booking_waived,
    publish_community_event,
    restore_community_event,
    update_community_event,
)
from apps.user_service.app.schemas.community_events import (
    CancelCommunityEventRequest,
    CommunityEventBookingListQuery,
    CommunityEventDetailResponse,
    CommunityEventExportQuery,
    CommunityEventListQuery,
    CommunityEventSummaryResponse,
    CreateCommunityEventRequest,
    MarkBookingPaidRequest,
    MarkBookingWaivedRequest,
    UpdateCommunityEventRequest,
)
from apps.user_service.app.schemas.enums import (
    CommunityEventCategory,
    CommunityEventPublishMode,
    CommunityEventPublishStatus,
    CommunityEventType,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
EVENT_ID = "22222222-2222-2222-2222-222222222222"
BOOKING_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def _skip_audit_logging():
    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_decorator._log_audit_event",
        new_callable=AsyncMock,
    ):
        yield


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


def _user_context() -> UserContext:
    return UserContext(user_id="staff-1", email="staff@example.com", organization_id="org-1")


def _detail(**overrides) -> CommunityEventDetailResponse:
    now = datetime.now(timezone.utc)
    today = date.today()
    base = {
        "id": EVENT_ID,
        "display_code": "EVT-1",
        "title": "Summer fest",
        "description": "",
        "category": CommunityEventCategory.SOCIAL.value,
        "category_label": "Social",
        "publish_status": CommunityEventPublishStatus.DRAFT.value,
        "record_status": "active",
        "is_multi_day": False,
        "start_date": today,
        "end_date": today,
        "event_type": CommunityEventType.FREE.value,
        "max_tickets_per_resident": 4,
        "adult_price_minor": 0,
        "child_ticket_mode": "not_applicable",
        "child_price_minor": 0,
        "apply_tax": False,
        "tax_rate": 18.0,
        "currency": "INR",
        "gallery": [],
        "tickets_booked": 0,
        "bookings_count": 0,
        "paid_bookings_count": 0,
        "revenue_collected_minor": 0,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return CommunityEventDetailResponse(**base)


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.community_events.ensure_staff_project_access", new_callable=AsyncMock
)
@patch("apps.user_service.app.api.community_events.CommunityEventsService")
async def test_event_read_endpoints(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.get_summary = AsyncMock(
        return_value=CommunityEventSummaryResponse(
            total_events=1,
            upcoming=1,
            total_rsvps=0,
            revenue_collected_minor=0,
            tabs={
                "all": 1,
                "draft": 1,
                "published": 0,
                "completed": 0,
                "cancelled": 0,
                "deleted": 0,
            },
        )
    )
    service.list_events = AsyncMock(return_value=([], 0))
    service.get_event = AsyncMock(return_value=_detail())
    service.export_events_csv = AsyncMock(return_value="title\nSummer fest\n")
    service.list_bookings = AsyncMock(return_value=([], 0))
    service.export_bookings_csv = AsyncMock(return_value="booking\n")

    assert (
        await get_community_events_summary(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await list_community_events(
            request=_request(),
            project_id=PROJECT_ID,
            query=CommunityEventListQuery(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await get_community_event(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await export_community_events(
            request=_request(),
            project_id=PROJECT_ID,
            query=CommunityEventExportQuery(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await list_community_event_bookings(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            query=CommunityEventBookingListQuery(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await export_community_event_bookings(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.community_events.ensure_staff_project_access", new_callable=AsyncMock
)
@patch("apps.user_service.app.api.community_events.CommunityEventsService")
async def test_event_write_endpoints(mock_events_cls, mock_access):
    mock_access.return_value = _user_context()
    events = mock_events_cls.return_value
    events.create_event = AsyncMock(return_value=_detail())
    events.update_event = AsyncMock(return_value=_detail())
    events.publish_event = AsyncMock(return_value=_detail(publish_status="published"))
    events.cancel_event = AsyncMock(return_value=_detail(publish_status="cancelled"))
    events.complete_event = AsyncMock(return_value=_detail(publish_status="completed"))
    events.delete_event = AsyncMock(return_value=_detail(record_status="deleted"))
    events.restore_event = AsyncMock(return_value=_detail())
    events.mark_booking_paid = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"id": BOOKING_ID})
    )
    events.mark_booking_waived = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"id": BOOKING_ID})
    )

    body = CreateCommunityEventRequest(
        title="Summer fest",
        start_date=date.today(),
        end_date=date.today(),
        publish_mode=CommunityEventPublishMode.DRAFT,
    )
    user = {"sub": "staff-1"}
    db = MagicMock()
    assert (
        await create_community_event(
            request=_request(),
            project_id=PROJECT_ID,
            body=body,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 201
    assert (
        await update_community_event(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            body=UpdateCommunityEventRequest(title="Updated"),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await publish_community_event(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await cancel_community_event(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            body=CancelCommunityEventRequest(reason="Rain"),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await complete_community_event(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await delete_community_event(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await restore_community_event(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await mark_community_event_booking_paid(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            booking_id=BOOKING_ID,
            body=MarkBookingPaidRequest(payment_notes="Cash"),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await mark_community_event_booking_waived(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            booking_id=BOOKING_ID,
            body=MarkBookingWaivedRequest(payment_notes="Comp"),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.community_events.ensure_staff_project_access", new_callable=AsyncMock
)
@patch("apps.user_service.app.api.community_events.CommunityEventsService")
async def test_get_event_not_found(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.get_event = AsyncMock(
        side_effect=NotFoundException(
            message_key="community_events.errors.event_not_found",
            custom_code=404,
        )
    )
    with pytest.raises(NotFoundException):
        await get_community_event(
            request=_request(),
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
