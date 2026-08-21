"""Unit tests for CommunityEventNotificationService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.services.community_event_notification_service import (
    CommunityEventNotificationService,
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
