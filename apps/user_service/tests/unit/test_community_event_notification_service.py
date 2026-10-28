"""Unit tests for CommunityEventNotificationService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums import (
    CommunityEventBookingStatus,
    CommunityEventPaymentStatus,
    CommunityEventPublishStatus,
    CommunityEventRecordStatus,
)
from apps.user_service.app.services.community_event_notification_service import (
    CommunityEventNotificationService,
    can_resend_event_tickets,
    ticket_items_for_booking,
    ticket_push_data,
    tickets_are_issued,
)

ORG = "11111111-1111-1111-1111-111111111111"
EVENT = "22222222-2222-2222-2222-222222222222"
BOOKING = "33333333-3333-3333-3333-333333333333"


def _service() -> CommunityEventNotificationService:
    svc = CommunityEventNotificationService(db_connection=MagicMock())
    svc.push_dispatcher = MagicMock()
    svc.push_dispatcher.send_to_user = AsyncMock()
    svc.contacts_repo = MagicMock()
    return svc


@pytest.mark.asyncio
async def test_send_to_contact_skips_without_user():
    svc = _service()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value=None)
    await svc.notify_booking_confirmed(
        organization_id=ORG,
        contact_id="contact-1",
        event={"id": EVENT, "title": "Fest"},
        booking={"id": BOOKING},
    )
    svc.push_dispatcher.send_to_user.assert_not_awaited()

    svc.contacts_repo.get_contact_details = AsyncMock(return_value={"user_id": None})
    await svc.notify_booking_confirmed(
        organization_id=ORG,
        contact_id="contact-1",
        event={"id": EVENT, "title": "Fest"},
        booking={"id": BOOKING},
    )
    svc.push_dispatcher.send_to_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_to_contact_success():
    svc = _service()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value={"user_id": "user-1"})
    await svc.notify_booking_confirmed(
        organization_id=ORG,
        contact_id="contact-1",
        event={"id": EVENT, "title": "Fest"},
        booking={"id": BOOKING},
    )
    svc.push_dispatcher.send_to_user.assert_awaited_once()
    call_kwargs = svc.push_dispatcher.send_to_user.await_args.kwargs
    assert call_kwargs["params"] == {"title": "Fest"}
    assert call_kwargs["data"]["title"] == "Fest"


@pytest.mark.asyncio
async def test_send_to_contact_swallows_push_errors():
    svc = _service()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value={"user_id": "user-1"})
    svc.push_dispatcher.send_to_user = AsyncMock(side_effect=RuntimeError("grpc down"))
    await svc.notify_event_reminder(
        organization_id=ORG,
        contact_id="contact-1",
        event={"id": EVENT, "title": "Fest"},
    )


@pytest.mark.asyncio
async def test_notify_event_published_sends_to_each_user():
    svc = _service()
    svc.push_dispatcher.send_to_user = AsyncMock()
    await svc.notify_event_published(
        organization_id=ORG,
        event={"id": EVENT, "title": "Fest", "project_id": "project-1"},
        recipient_user_ids=["user-1", "user-2"],
    )
    assert svc.push_dispatcher.send_to_user.await_count == 2
    call_kwargs = svc.push_dispatcher.send_to_user.await_args.kwargs
    assert call_kwargs["params"] == {"title": "Fest"}


@pytest.mark.asyncio
async def test_notify_event_published_swallows_individual_failures():
    svc = _service()
    svc.push_dispatcher.send_to_user = AsyncMock(side_effect=RuntimeError("fail"))
    with patch("apps.user_service.app.services.community_event_notification_service.logger"):
        await svc.notify_event_published(
            organization_id=ORG,
            event={"id": EVENT, "title": "Fest", "project_id": "project-1"},
            recipient_user_ids=["user-1"],
        )


@pytest.mark.asyncio
async def test_other_notification_wrappers():
    svc = _service()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value={"user_id": "user-1"})
    event = {"id": EVENT, "title": "Fest"}
    booking = {"id": BOOKING}

    for notify_fn in (
        svc.notify_booking_waitlisted,
        svc.notify_waitlist_promoted,
        svc.notify_payment_received,
    ):
        svc.push_dispatcher.send_to_user.reset_mock()
        await notify_fn(
            organization_id=ORG,
            contact_id="contact-1",
            event=event,
            booking=booking,
        )
        svc.push_dispatcher.send_to_user.assert_awaited_once()

    svc.push_dispatcher.send_to_user.reset_mock()
    await svc.notify_event_reminder(
        organization_id=ORG,
        contact_id="contact-1",
        event=event,
    )
    svc.push_dispatcher.send_to_user.assert_awaited_once()

    svc.push_dispatcher.send_to_user.reset_mock()
    await svc.notify_event_cancelled(
        organization_id=ORG,
        contact_id="contact-1",
        event=event,
    )
    svc.push_dispatcher.send_to_user.assert_awaited_once()
    call_kwargs = svc.push_dispatcher.send_to_user.await_args.kwargs
    assert call_kwargs["message_key"] == "notifications.push.community_events.cancelled"


@pytest.mark.asyncio
async def test_notify_booking_confirmed_includes_stable_ticket_codes():
    svc = _service()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value={"user_id": "user-1"})
    await svc.notify_booking_confirmed(
        organization_id=ORG,
        contact_id="contact-1",
        event={"id": EVENT, "title": "Brazil fest", "project_id": "project-1"},
        booking={
            "id": BOOKING,
            "display_code": "BKG-36",
            "adult_tickets": 2,
            "child_tickets": 0,
            "total_tickets": 2,
            "total_amount_minor": 22000,
            "currency": "INR",
            "gate_qr_token": "original-qr",
        },
    )
    data = svc.push_dispatcher.send_to_user.await_args.kwargs["data"]
    assert data["ticket_codes"] == "BKG-36-A1, BKG-36-A2"
    assert data["gate_qr_token"] == "original-qr"


@pytest.mark.asyncio
async def test_notify_tickets_resent_replays_same_payload():
    svc = _service()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value={"user_id": "user-1"})
    sent = await svc.notify_tickets_resent(
        organization_id=ORG,
        contact_id="contact-1",
        event={"id": EVENT, "title": "Brazil fest", "project_id": "project-1"},
        booking={
            "id": BOOKING,
            "display_code": "BKG-36",
            "adult_tickets": 2,
            "child_tickets": 0,
            "total_tickets": 2,
            "gate_qr_token": "original-qr",
        },
    )
    assert sent is True
    call_kwargs = svc.push_dispatcher.send_to_user.await_args.kwargs
    assert call_kwargs["message_key"] == "notifications.push.community_events.tickets_resent"
    assert call_kwargs["params"]["ticket_codes"] == "BKG-36-A1, BKG-36-A2"
    assert call_kwargs["data"]["gate_qr_token"] == "original-qr"


@pytest.mark.asyncio
async def test_notify_tickets_resent_returns_false_without_user():
    svc = _service()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value={"user_id": None})
    sent = await svc.notify_tickets_resent(
        organization_id=ORG,
        contact_id="contact-1",
        event={"id": EVENT, "title": "Fest"},
        booking={"id": BOOKING, "display_code": "BKG-1"},
    )
    assert sent is False
    svc.push_dispatcher.send_to_user.assert_not_awaited()


def test_ticket_items_stable_adult_and_child_codes() -> None:
    items = ticket_items_for_booking(
        {
            "display_code": "BKG-36",
            "adult_tickets": 2,
            "child_tickets": 1,
        }
    )
    assert items == [
        {"code": "BKG-36-A1", "ticket_type": "adult"},
        {"code": "BKG-36-A2", "ticket_type": "adult"},
        {"code": "BKG-36-C1", "ticket_type": "child"},
    ]


def test_ticket_items_empty_without_display_code() -> None:
    assert ticket_items_for_booking({"adult_tickets": 2, "child_tickets": 0}) == []


def test_tickets_are_issued_only_for_confirmed_settled_bookings() -> None:
    confirmed_paid = {
        "booking_status": CommunityEventBookingStatus.CONFIRMED.value,
        "payment_status": CommunityEventPaymentStatus.PAID.value,
    }
    assert tickets_are_issued(confirmed_paid) is True
    assert (
        tickets_are_issued(
            {
                "booking_status": CommunityEventBookingStatus.CONFIRMED.value,
                "payment_status": CommunityEventPaymentStatus.PENDING.value,
            }
        )
        is False
    )
    assert (
        tickets_are_issued(
            {
                "booking_status": CommunityEventBookingStatus.WAITLISTED.value,
                "payment_status": CommunityEventPaymentStatus.NOT_APPLICABLE.value,
            }
        )
        is False
    )


def test_can_resend_requires_active_published_or_completed_event() -> None:
    booking = {
        "booking_status": CommunityEventBookingStatus.CONFIRMED.value,
        "payment_status": CommunityEventPaymentStatus.NOT_APPLICABLE.value,
    }
    assert can_resend_event_tickets(booking) is True
    assert (
        can_resend_event_tickets(
            booking,
            {
                "publish_status": CommunityEventPublishStatus.COMPLETED.value,
                "record_status": CommunityEventRecordStatus.ACTIVE.value,
            },
        )
        is True
    )
    assert (
        can_resend_event_tickets(
            booking,
            {
                "publish_status": CommunityEventPublishStatus.CANCELLED.value,
                "record_status": CommunityEventRecordStatus.ACTIVE.value,
            },
        )
        is False
    )


def test_ticket_push_data_reuses_existing_qr_and_codes() -> None:
    payload = ticket_push_data(
        event={"id": "evt-1", "title": "Brazil fest", "project_id": "proj-1"},
        booking={
            "id": "bkg-1",
            "display_code": "BKG-36",
            "adult_tickets": 2,
            "child_tickets": 0,
            "total_tickets": 2,
            "total_amount_minor": 22000,
            "currency": "INR",
            "gate_qr_token": "original-qr",
        },
    )
    assert payload["ticket_codes"] == "BKG-36-A1, BKG-36-A2"
    assert payload["gate_qr_token"] == "original-qr"
    assert payload["display_code"] == "BKG-36"
