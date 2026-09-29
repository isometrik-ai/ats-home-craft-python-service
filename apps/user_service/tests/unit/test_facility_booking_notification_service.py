"""Unit tests for FacilityBookingNotificationService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.services.facility_booking_notification_service import (
    FacilityBookingNotificationService,
)

ORG = "11111111-1111-1111-1111-111111111111"
CONTACT = "33333333-3333-3333-3333-333333333333"
RESERVATION = "22222222-2222-2222-2222-222222222222"


def _service() -> FacilityBookingNotificationService:
    dispatcher = MagicMock()
    dispatcher.send_to_contact = AsyncMock()
    dispatcher.send_to_org_members = AsyncMock()
    return FacilityBookingNotificationService(
        db_connection=MagicMock(),
        organization_id=ORG,
        dispatcher=dispatcher,
    )


@pytest.mark.asyncio
async def test_confirmed_charge_text_variants() -> None:
    svc = _service()
    await svc.confirmed(
        contact_id=CONTACT,
        reservation_id=RESERVATION,
        facility_label="Tennis",
        total=0,
        billed_later=False,
    )
    params = svc.dispatcher.send_to_contact.await_args.kwargs["params"]
    assert "no charge" in params["charge_text"].lower()

    svc.dispatcher.send_to_contact.reset_mock()
    await svc.confirmed(
        contact_id=CONTACT,
        reservation_id=RESERVATION,
        facility_label="Tennis",
        total=500,
        billed_later=True,
    )
    params = svc.dispatcher.send_to_contact.await_args.kwargs["params"]
    assert "invoice" in params["charge_text"].lower()

    svc.dispatcher.send_to_contact.reset_mock()
    await svc.confirmed(
        contact_id=CONTACT,
        reservation_id=RESERVATION,
        facility_label="Tennis",
        total=500,
        billed_later=False,
    )
    params = svc.dispatcher.send_to_contact.await_args.kwargs["params"]
    assert "charged" in params["charge_text"].lower()


@pytest.mark.asyncio
async def test_lifecycle_contact_notifications() -> None:
    svc = _service()
    for method, kwargs in (
        (svc.submitted, {"facility_label": "Hall"}),
        (svc.approved, {"facility_label": "Hall", "deposit": 0}),
        (svc.rejected, {"facility_label": "Hall", "reason": "Full"}),
        (svc.checked_in, {"facility_label": "Hall"}),
        (svc.no_show, {"facility_label": "Hall", "forfeit": 100}),
    ):
        svc.dispatcher.send_to_contact.reset_mock()
        await method(contact_id=CONTACT, reservation_id=RESERVATION, **kwargs)
        svc.dispatcher.send_to_contact.assert_awaited_once()

    svc.dispatcher.send_to_contact.reset_mock()
    await svc.approved(
        contact_id=CONTACT,
        reservation_id=RESERVATION,
        facility_label="Hall",
        deposit=200,
    )
    assert "Deposit" in svc.dispatcher.send_to_contact.await_args.kwargs["params"]["deposit_text"]


@pytest.mark.asyncio
async def test_cancelled_paths() -> None:
    svc = _service()
    await svc.cancelled(
        contact_id=CONTACT,
        reservation_id=RESERVATION,
        facility_label="Pool",
        by_staff=True,
        free=True,
        fee=0,
        refund=0,
        reason=None,
    )
    assert "cancelled_by_staff" in svc.dispatcher.send_to_contact.await_args.kwargs["message_key"]

    svc.dispatcher.send_to_contact.reset_mock()
    await svc.cancelled(
        contact_id=CONTACT,
        reservation_id=RESERVATION,
        facility_label="Pool",
        by_staff=False,
        free=True,
        fee=0,
        refund=0,
        reason=None,
    )
    assert "No charges" in svc.dispatcher.send_to_contact.await_args.kwargs["params"]["summary"]

    svc.dispatcher.send_to_contact.reset_mock()
    await svc.cancelled(
        contact_id=CONTACT,
        reservation_id=RESERVATION,
        facility_label="Pool",
        by_staff=False,
        free=False,
        fee=50,
        refund=150,
        reason="Changed plans",
    )
    assert (
        "Cancellation fee" in svc.dispatcher.send_to_contact.await_args.kwargs["params"]["summary"]
    )


@pytest.mark.asyncio
async def test_rescheduled_and_approval_requested() -> None:
    svc = _service()
    await svc.rescheduled(
        contact_id=CONTACT,
        reservation_id=RESERVATION,
        facility_label="Court",
        by_staff=False,
        pending=False,
    )
    key = svc.dispatcher.send_to_contact.await_args.kwargs["message_key"]
    assert key.endswith(".rescheduled")

    svc.dispatcher.send_to_contact.reset_mock()
    svc.dispatcher.send_to_org_members.reset_mock()
    await svc.rescheduled(
        contact_id=CONTACT,
        reservation_id=RESERVATION,
        facility_label="Court",
        by_staff=True,
        pending=False,
    )
    assert "rescheduled_by_staff" in svc.dispatcher.send_to_contact.await_args.kwargs["message_key"]

    svc.dispatcher.send_to_contact.reset_mock()
    svc.dispatcher.send_to_org_members.reset_mock()
    await svc.rescheduled(
        contact_id=CONTACT,
        reservation_id=RESERVATION,
        facility_label="Court",
        by_staff=True,
        pending=True,
    )
    assert svc.dispatcher.send_to_contact.await_count == 1
    assert "reschedule_submitted" in svc.dispatcher.send_to_contact.await_args.kwargs["message_key"]
    svc.dispatcher.send_to_org_members.assert_awaited_once()

    svc.dispatcher.send_to_org_members.reset_mock()
    await svc.approval_requested(
        reservation_id=RESERVATION,
        facility_label="Court",
        host_name="Ada",
    )
    svc.dispatcher.send_to_org_members.assert_awaited_once()


@pytest.mark.asyncio
async def test_billing_notifications() -> None:
    svc = _service()
    await svc.invoice_generated(
        contact_id=CONTACT,
        invoice_id="inv-1",
        number="INV-1",
        total=900,
        due_date="2026-10-01",
    )
    extra = svc.dispatcher.send_to_contact.await_args.kwargs["data"]
    assert extra["invoice_id"] == "inv-1"
    assert svc.dispatcher.send_to_contact.await_args.kwargs["entity"] is None

    svc.dispatcher.send_to_contact.reset_mock()
    await svc.payment_received(
        contact_id=CONTACT, invoice_id="inv-1", number="INV-1", method="wallet"
    )
    await svc.wallet_updated(contact_id=CONTACT, amount=100, reason="Goodwill")
    await svc.wallet_updated(contact_id=CONTACT, amount=-50, reason="Correction")
    assert svc.dispatcher.send_to_contact.await_count == 3

    svc.dispatcher.send_to_contact.reset_mock()
    await svc.charge_posted(
        contact_id=CONTACT,
        reservation_id=RESERVATION,
        amount=250,
        description="Damage fee",
    )
    svc.dispatcher.send_to_contact.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_swallows_errors() -> None:
    svc = _service()
    svc.dispatcher.send_to_contact = AsyncMock(side_effect=RuntimeError("down"))
    with patch(
        "apps.user_service.app.services.facility_booking_notification_service.logger"
    ) as log:
        await svc.submitted(contact_id=CONTACT, reservation_id=RESERVATION, facility_label="Hall")
        log.exception.assert_called_once()

    svc.dispatcher.send_to_org_members = AsyncMock(side_effect=RuntimeError("down"))
    with patch(
        "apps.user_service.app.services.facility_booking_notification_service.logger"
    ) as log:
        await svc.approval_requested(
            reservation_id=RESERVATION, facility_label="Hall", host_name="Ada"
        )
        log.exception.assert_called_once()
