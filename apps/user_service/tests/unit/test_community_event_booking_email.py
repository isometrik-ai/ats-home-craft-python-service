"""Unit tests for community event booking confirmation email."""

from __future__ import annotations

from datetime import date, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums import CommunityEventPaymentStatus
from apps.user_service.app.services.community_event_notification_service import (
    CommunityEventNotificationService,
)
from apps.user_service.app.services.community_event_ticket_pdf import (
    build_community_event_ticket_pdf,
)
from apps.user_service.app.utils.community_event_email_helpers import (
    build_booking_email_body_context,
    build_ticket_attachments,
    contact_booking_email,
    tickets_label,
)
from apps.user_service.app.utils.file_email_renderer import (
    BODY_CONTENT_TOKEN,
    render_email,
)
from apps.user_service.app.utils.qr_code import qr_matrix, qr_png_bytes

ORG = "11111111-1111-1111-1111-111111111111"
EVENT_ID = "22222222-2222-2222-2222-222222222222"
BOOKING_ID = "33333333-3333-3333-3333-333333333333"
PROJECT_ID = "44444444-4444-4444-4444-444444444444"


def _contact(**overrides) -> dict:
    base = {
        "first_name": "Jane",
        "emails": [{"email": "jane@example.com", "is_primary": True}],
        "user_id": "user-1",
    }
    base.update(overrides)
    return base


def _event(**overrides) -> dict:
    base = {
        "id": EVENT_ID,
        "title": "Pottery Workshop",
        "project_id": PROJECT_ID,
        "start_date": date(2026, 8, 19),
        "start_time": time(16, 0),
        "facility_name": "Activity Room",
        "tower_name": "Tower B",
        "floor_level": "Ground Floor",
    }
    base.update(overrides)
    return base


def _booking(**overrides) -> dict:
    base = {
        "id": BOOKING_ID,
        "display_code": "BKG-501",
        "project_id": PROJECT_ID,
        "adult_tickets": 2,
        "child_tickets": 1,
        "total_tickets": 3,
        "total_amount_minor": 139000,
        "payment_status": CommunityEventPaymentStatus.PENDING.value,
        "gate_qr_token": "a" * 32,
    }
    base.update(overrides)
    return base


def test_qr_has_finder_patterns_and_png_header() -> None:
    matrix = qr_matrix("a" * 32)
    assert len(matrix) == len(matrix[0])
    assert matrix[0][0] == 1
    assert matrix[0][6] == 1
    assert matrix[3][3] == 1
    png = qr_png_bytes("a" * 32)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_ticket_pdf_header() -> None:
    pdf = build_community_event_ticket_pdf(
        {
            "event_title": "Pottery Workshop",
            "display_code": "BKG-501",
            "event_when": "19 August 2026, 4:00 PM",
            "venue": "Activity Room",
            "tickets_label": "2 adult, 1 child (3 total)",
            "amount_label": "Rs 1,390.00",
            "payment_label": "Pay at the clubhouse (pending)",
            "gate_qr_token": "a" * 32,
        }
    )
    assert pdf.startswith(b"%PDF-1.4")
    assert b"BKG-501" in pdf


def test_contact_booking_email_prefers_primary() -> None:
    assert contact_booking_email(_contact()) == "jane@example.com"
    assert contact_booking_email({"email": "auth@example.com"}) == "auth@example.com"
    assert contact_booking_email({}) is None


def test_tickets_label() -> None:
    assert tickets_label(adult_tickets=2, child_tickets=1, total_tickets=3) == (
        "2 adult, 1 child (3 total)"
    )


def test_build_body_context_and_attachments() -> None:
    context = build_booking_email_body_context(
        contact=_contact(),
        event=_event(),
        booking=_booking(),
        project_name="Sunrise Towers",
    )
    assert context["display_code"] == "BKG-501"
    assert context["tickets_label"] == "2 adult, 1 child (3 total)"
    assert "19 August 2026" in context["event_when"]
    assert "4:00 PM" in context["event_when"]
    assert context["qr_html"].startswith("<table")
    attachments = build_ticket_attachments(booking=_booking(), event=_event())
    assert len(attachments) == 2
    assert attachments[0]["filename"] == "BKG-501-ticket.pdf"
    assert attachments[1]["filename"] == "BKG-501-qr.png"
    assert (
        build_ticket_attachments(
            booking=_booking(gate_qr_token=None),
            event=_event(),
        )
        == []
    )


def test_render_confirmation_email() -> None:
    context = build_booking_email_body_context(
        contact=_contact(),
        event=_event(),
        booking=_booking(),
        project_name="Sunrise Towers",
    )
    plain, html, subject = render_email(
        body="community_event_booking_confirmed",
        body_context=context,
    )
    assert "BKG-501" in subject
    assert "Pottery Workshop" in subject
    assert "2 adult, 1 child (3 total)" in plain
    assert "BKG-501" in html
    assert BODY_CONTENT_TOKEN not in html
    assert "<script>" not in html


@pytest.mark.asyncio
async def test_notify_booking_confirmed_sends_ticket_email() -> None:
    svc = CommunityEventNotificationService(db_connection=MagicMock())
    svc.push_dispatcher = MagicMock()
    svc.push_dispatcher.send_to_user = AsyncMock()
    svc.contacts_repo = MagicMock()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value=_contact())
    svc.db_connection.fetchrow = AsyncMock(return_value={"name": "Sunrise Towers"})

    with patch(
        "apps.user_service.app.services.community_event_notification_service.send_community_event_booking_email",
        return_value=True,
    ) as mock_send:
        await svc.notify_booking_confirmed(
            organization_id=ORG,
            contact_id="contact-1",
            event=_event(),
            booking=_booking(),
        )

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["email"] == "jane@example.com"
    assert kwargs["template"] == "community_event_booking_confirmed"
    assert kwargs["body_context"]["display_code"] == "BKG-501"
    assert len(kwargs["attachments"]) == 2


@pytest.mark.asyncio
async def test_notify_booking_waitlisted_email_has_no_ticket() -> None:
    svc = CommunityEventNotificationService(db_connection=MagicMock())
    svc.push_dispatcher = MagicMock()
    svc.push_dispatcher.send_to_user = AsyncMock()
    svc.contacts_repo = MagicMock()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value=_contact())
    svc.db_connection.fetchrow = AsyncMock(return_value={"name": "Sunrise Towers"})

    with patch(
        "apps.user_service.app.services.community_event_notification_service.send_community_event_booking_email",
        return_value=True,
    ) as mock_send:
        await svc.notify_booking_waitlisted(
            organization_id=ORG,
            contact_id="contact-1",
            event=_event(),
            booking=_booking(gate_qr_token=None),
        )

    kwargs = mock_send.call_args.kwargs
    assert kwargs["template"] == "community_event_booking_waitlisted"
    assert kwargs["attachments"] is None


@pytest.mark.asyncio
async def test_notify_skips_email_without_address() -> None:
    svc = CommunityEventNotificationService(db_connection=MagicMock())
    svc.push_dispatcher = MagicMock()
    svc.push_dispatcher.send_to_user = AsyncMock()
    svc.contacts_repo = MagicMock()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value={"user_id": "user-1"})

    with patch(
        "apps.user_service.app.services.community_event_notification_service.send_community_event_booking_email"
    ) as mock_send:
        await svc.notify_booking_confirmed(
            organization_id=ORG,
            contact_id="contact-1",
            event=_event(),
            booking=_booking(),
        )

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_send_ticket_email_requires_address() -> None:
    from libs.shared_utils.http_exceptions import ValidationException

    svc = CommunityEventNotificationService(db_connection=MagicMock())
    svc.contacts_repo = MagicMock()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value={"user_id": "user-1"})

    with pytest.raises(ValidationException):
        await svc.send_ticket_email(
            organization_id=ORG,
            contact_id="contact-1",
            event=_event(),
            booking=_booking(),
        )


@pytest.mark.asyncio
async def test_send_ticket_email_sends_one_mail() -> None:
    svc = CommunityEventNotificationService(db_connection=MagicMock())
    svc.push_dispatcher = MagicMock()
    svc.push_dispatcher.send_to_user = AsyncMock()
    svc.contacts_repo = MagicMock()
    svc.contacts_repo.get_contact_details = AsyncMock(return_value=_contact())
    svc.db_connection.fetchrow = AsyncMock(return_value={"name": "Sunrise Towers"})

    with patch(
        "apps.user_service.app.services.community_event_notification_service.send_community_event_booking_email",
        return_value=True,
    ) as mock_send:
        email = await svc.send_ticket_email(
            organization_id=ORG,
            contact_id="contact-1",
            event=_event(),
            booking=_booking(),
        )

    assert email == "jane@example.com"
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["template"] == "community_event_booking_confirmed"
    assert len(kwargs["attachments"]) == 2
    svc.push_dispatcher.send_to_user.assert_not_awaited()
