"""Unit tests for community event booking service."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.community_events import (
    CreateEventBookingRequest,
    MarkBookingPaidRequest,
)
from apps.user_service.app.schemas.enums import (
    CommunityEventBookingStatus,
    CommunityEventChildTicketMode,
    CommunityEventPaymentStatus,
    CommunityEventPublishStatus,
    CommunityEventRecordStatus,
    CommunityEventType,
)
from apps.user_service.app.services.community_event_booking_service import (
    CommunityEventBookingService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
EVENT_ID = "22222222-2222-2222-2222-222222222222"
BOOKING_ID = "33333333-3333-3333-3333-333333333333"
CONTACT_ID = "44444444-4444-4444-4444-444444444444"
UNIT_ID = "55555555-5555-5555-5555-555555555555"


def _service() -> CommunityEventBookingService:
    svc = CommunityEventBookingService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="user-1",
            email="resident@example.com",
            organization_id="org-1",
        ),
    )
    svc.repo = MagicMock()
    svc.contact_units_repo = MagicMock()
    svc.notifications = MagicMock()
    svc.notifications.notify_booking_confirmed = AsyncMock()
    svc.notifications.notify_booking_waitlisted = AsyncMock()
    svc.notifications.notify_waitlist_promoted = AsyncMock()
    svc.notifications.notify_payment_received = AsyncMock()
    return svc


def _published_event(**overrides) -> dict:
    base = {
        "event_type": CommunityEventType.PAID.value,
        "publish_status": CommunityEventPublishStatus.PUBLISHED.value,
        "record_status": CommunityEventRecordStatus.ACTIVE.value,
        "booking_closes_at": datetime.now(timezone.utc) + timedelta(days=1),
        "end_date": date.today() + timedelta(days=2),
        "end_time": None,
        "adult_price_minor": 58900,
        "child_ticket_mode": CommunityEventChildTicketMode.FREE.value,
        "child_price_minor": 0,
        "apply_tax": True,
        "tax_rate": 18.0,
        "tickets_booked": 0,
        "total_capacity": 100,
    }
    base.update(overrides)
    return base


class TestComputeBookingAmounts:
    def test_free_event_zero_amounts(self) -> None:
        event = _published_event(event_type=CommunityEventType.FREE.value)
        subtotal, tax, total = CommunityEventBookingService.compute_booking_amounts(
            adult_tickets=2,
            child_tickets=1,
            event=event,
        )
        assert subtotal == 0
        assert tax == 0
        assert total == 0

    def test_paid_event_with_tax(self) -> None:
        event = _published_event()
        subtotal, tax, total = CommunityEventBookingService.compute_booking_amounts(
            adult_tickets=2,
            child_tickets=1,
            event=event,
        )
        assert subtotal == 117800
        assert tax == 21204
        assert total == 139004

    def test_paid_event_no_tax(self) -> None:
        event = _published_event(apply_tax=False)
        subtotal, tax, total = CommunityEventBookingService.compute_booking_amounts(
            adult_tickets=1,
            child_tickets=1,
            event=event,
        )
        assert subtotal == 58900
        assert tax == 0
        assert total == 58900


class TestBookingOpen:
    def test_closed_when_past_deadline(self) -> None:
        event = _published_event(
            booking_closes_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert CommunityEventBookingService._is_booking_open(event) is False

    def test_open_when_published_and_future_deadline(self) -> None:
        event = _published_event()
        assert CommunityEventBookingService._is_booking_open(event) is True


class TestFacilityValidation:
    def test_rejects_parking_facility(self) -> None:
        with pytest.raises(ValidationException):
            CommunityEventBookingService.validate_facility_for_event(
                {"facility_type": "parking", "status": "active", "active": True}
            )

    def test_accepts_events_facility(self) -> None:
        CommunityEventBookingService.validate_facility_for_event(
            {"facility_type": "events", "status": "active", "active": True}
        )

    def test_accepts_sports_facility(self) -> None:
        CommunityEventBookingService.validate_facility_for_event(
            {"facility_type": "sports", "status": "active", "active": True}
        )

    def test_rejects_missing_facility(self) -> None:
        with pytest.raises(ValidationException):
            CommunityEventBookingService.validate_facility_for_event(None)

    def test_rejects_inactive_facility(self) -> None:
        with pytest.raises(ValidationException):
            CommunityEventBookingService.validate_facility_for_event(
                {"facility_type": "events", "status": "inactive", "active": True}
            )


class TestEventEndAt:
    def test_uses_end_time_when_present(self) -> None:
        end = CommunityEventBookingService._event_end_at(
            {
                "end_date": date(2026, 6, 1),
                "end_time": datetime.strptime("18:00", "%H:%M").time(),
            }
        )
        assert end.hour == 18


@pytest.mark.asyncio
async def test_create_booking_confirmed():
    svc = _service()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": PROJECT_ID})
    svc.repo.contact_has_owner_or_tenant_on_unit = AsyncMock(return_value=True)
    event = _published_event(project_id=PROJECT_ID, id=EVENT_ID)
    svc.repo.fetch_resident_event_by_id = AsyncMock(return_value=event)
    svc.repo.count_active_tickets_for_contact = AsyncMock(return_value=0)
    svc.repo.allocate_booking_sequence = AsyncMock(return_value=1)
    svc.repo.insert_booking = AsyncMock(
        return_value={
            "id": BOOKING_ID,
            "display_code": "BKG-1",
            "currency": "INR",
        }
    )
    svc.repo.adjust_event_aggregates_on_booking = AsyncMock()
    svc.repo.insert_audit_log = AsyncMock()

    result = await svc.create_booking(
        contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
        event_id=EVENT_ID,
        body=CreateEventBookingRequest(adult_tickets=2, child_tickets=0),
    )

    assert result.booking_status == CommunityEventBookingStatus.CONFIRMED.value
    assert result.payment_status == CommunityEventPaymentStatus.PENDING.value
    svc.notifications.notify_booking_confirmed.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_booking_waitlisted_when_full():
    svc = _service()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": PROJECT_ID})
    svc.repo.contact_has_owner_or_tenant_on_unit = AsyncMock(return_value=True)
    event = _published_event(
        project_id=PROJECT_ID,
        id=EVENT_ID,
        total_capacity=10,
        tickets_booked=10,
    )
    svc.repo.fetch_resident_event_by_id = AsyncMock(return_value=event)
    svc.repo.count_active_tickets_for_contact = AsyncMock(return_value=0)
    svc.repo.allocate_booking_sequence = AsyncMock(return_value=2)
    svc.repo.insert_booking = AsyncMock(
        return_value={"id": BOOKING_ID, "display_code": "BKG-2", "currency": "INR"}
    )
    svc.repo.adjust_event_aggregates_on_booking = AsyncMock()
    svc.repo.insert_audit_log = AsyncMock()

    result = await svc.create_booking(
        contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
        event_id=EVENT_ID,
        body=CreateEventBookingRequest(adult_tickets=1, child_tickets=0),
    )

    assert result.booking_status == CommunityEventBookingStatus.WAITLISTED.value
    assert result.gate_qr_token is None
    svc.notifications.notify_booking_waitlisted.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_booking_failures():
    svc = _service()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=False)

    with pytest.raises(ValidationException):
        await svc.create_booking(
            contact_id=CONTACT_ID,
            unit_id=UNIT_ID,
            event_id=EVENT_ID,
            body=CreateEventBookingRequest(adult_tickets=1, child_tickets=0),
        )

    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.get_unit_project = AsyncMock(return_value={"project_id": PROJECT_ID})
    svc.repo.contact_has_owner_or_tenant_on_unit = AsyncMock(return_value=True)
    svc.repo.fetch_resident_event_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.create_booking(
            contact_id=CONTACT_ID,
            unit_id=UNIT_ID,
            event_id=EVENT_ID,
            body=CreateEventBookingRequest(adult_tickets=1, child_tickets=0),
        )

    closed = _published_event(
        project_id=PROJECT_ID,
        booking_closes_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    svc.repo.fetch_resident_event_by_id = AsyncMock(return_value=closed)
    with pytest.raises(ValidationException):
        await svc.create_booking(
            contact_id=CONTACT_ID,
            unit_id=UNIT_ID,
            event_id=EVENT_ID,
            body=CreateEventBookingRequest(adult_tickets=1, child_tickets=0),
        )


@pytest.mark.asyncio
async def test_cancel_booking_and_promote_waitlist():
    svc = _service()
    svc.repo.fetch_booking_by_id = AsyncMock(
        return_value={
            "id": BOOKING_ID,
            "event_id": EVENT_ID,
            "project_id": PROJECT_ID,
            "contact_id": CONTACT_ID,
            "unit_id": UNIT_ID,
            "booking_status": CommunityEventBookingStatus.CONFIRMED.value,
            "total_tickets": 2,
        }
    )
    svc.repo.update_booking_fields = AsyncMock()
    svc.repo.adjust_event_aggregates_on_booking = AsyncMock()
    svc.repo.insert_audit_log = AsyncMock()
    svc._promote_waitlist = AsyncMock()

    await svc.cancel_booking(
        contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
        booking_id=BOOKING_ID,
    )
    svc._promote_waitlist.assert_awaited_once_with(event_id=EVENT_ID)

    svc.repo.fetch_booking_by_id = AsyncMock(
        return_value={
            "id": BOOKING_ID,
            "booking_status": CommunityEventBookingStatus.CANCELLED.value,
        }
    )
    with pytest.raises(ConflictException):
        await svc.cancel_booking(
            contact_id=CONTACT_ID,
            unit_id=UNIT_ID,
            booking_id=BOOKING_ID,
        )


@pytest.mark.asyncio
async def test_mark_paid_success_and_conflict():
    svc = _service()
    svc.repo.fetch_booking_by_id = AsyncMock(
        return_value={
            "id": BOOKING_ID,
            "project_id": PROJECT_ID,
            "contact_id": CONTACT_ID,
            "payment_status": CommunityEventPaymentStatus.PENDING.value,
            "total_amount_minor": 5000,
        }
    )
    svc.repo.update_booking_fields = AsyncMock(return_value={"id": BOOKING_ID})
    svc.repo.increment_paid_revenue = AsyncMock()
    svc.repo.insert_audit_log = AsyncMock()
    svc.repo.fetch_event_by_id = AsyncMock(return_value={"id": EVENT_ID, "title": "Fest"})

    updated = await svc.mark_paid(
        project_id=PROJECT_ID,
        event_id=EVENT_ID,
        booking_id=BOOKING_ID,
        body=MarkBookingPaidRequest(payment_notes="Cash"),
    )
    assert updated["id"] == BOOKING_ID
    svc.notifications.notify_payment_received.assert_awaited_once()

    svc.repo.fetch_booking_by_id = AsyncMock(
        return_value={
            "id": BOOKING_ID,
            "project_id": PROJECT_ID,
            "payment_status": CommunityEventPaymentStatus.PAID.value,
        }
    )
    with pytest.raises(ConflictException):
        await svc.mark_paid(
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            booking_id=BOOKING_ID,
            body=MarkBookingPaidRequest(payment_notes="Cash"),
        )


@pytest.mark.asyncio
async def test_verify_booking_at_gate():
    svc = _service()
    svc.repo.fetch_booking_by_gate_token = AsyncMock(
        return_value={
            "id": BOOKING_ID,
            "display_code": "BKG-1",
            "project_id": PROJECT_ID,
            "event_id": EVENT_ID,
            "booking_status": CommunityEventBookingStatus.CONFIRMED.value,
            "event_title": "Fest",
            "event_start_date": date.today(),
            "adult_tickets": 2,
            "child_tickets": 0,
            "total_tickets": 2,
            "payment_status": "paid",
        }
    )
    svc.repo.insert_audit_log = AsyncMock()

    result = await svc.verify_booking_at_gate(gate_qr_token="token-123")
    assert result.booking_id == BOOKING_ID

    svc.repo.fetch_booking_by_gate_token = AsyncMock(
        return_value={
            "id": BOOKING_ID,
            "booking_status": CommunityEventBookingStatus.WAITLISTED.value,
        }
    )
    with pytest.raises(ValidationException):
        await svc.verify_booking_at_gate(gate_qr_token="token-123")


@pytest.mark.asyncio
async def test_mark_waived_success_and_conflict():
    from apps.user_service.app.schemas.community_events import MarkBookingWaivedRequest

    svc = _service()
    now = datetime.now(timezone.utc)
    pending_booking = {
        "id": BOOKING_ID,
        "project_id": PROJECT_ID,
        "event_id": EVENT_ID,
        "payment_status": CommunityEventPaymentStatus.PENDING.value,
    }
    svc.repo.fetch_booking_by_id = AsyncMock(return_value=pending_booking)
    svc.repo.update_booking_fields = AsyncMock(
        return_value={
            **pending_booking,
            "payment_status": CommunityEventPaymentStatus.WAIVED.value,
            "paid_at": now,
        }
    )
    svc.repo.insert_audit_log = AsyncMock()

    result = await svc.mark_waived(
        project_id=PROJECT_ID,
        event_id=EVENT_ID,
        booking_id=BOOKING_ID,
        body=MarkBookingWaivedRequest(payment_notes="Comp"),
    )
    assert result["payment_status"] == CommunityEventPaymentStatus.WAIVED.value

    svc.repo.fetch_booking_by_id = AsyncMock(
        return_value={
            **pending_booking,
            "payment_status": CommunityEventPaymentStatus.PAID.value,
        }
    )
    with pytest.raises(ConflictException):
        await svc.mark_waived(
            project_id=PROJECT_ID,
            event_id=EVENT_ID,
            booking_id=BOOKING_ID,
            body=MarkBookingWaivedRequest(),
        )


@pytest.mark.asyncio
async def test_promote_waitlist_on_cancel():
    svc = _service()
    svc.db_connection.fetchrow = AsyncMock(return_value={"project_id": PROJECT_ID})
    svc.repo.fetch_event_by_id = AsyncMock(
        return_value={
            "total_capacity": 10,
            "tickets_booked": 8,
        }
    )
    svc.repo.fetch_oldest_waitlisted_booking = AsyncMock(
        return_value={
            "id": "wait-1",
            "contact_id": CONTACT_ID,
            "total_tickets": 2,
        }
    )
    svc.repo.update_booking_fields = AsyncMock()
    svc.repo.adjust_event_aggregates_on_booking = AsyncMock()
    svc.notifications.notify_waitlist_promoted = AsyncMock()

    await svc._promote_waitlist(event_id=EVENT_ID)

    svc.repo.update_booking_fields.assert_awaited_once()
    svc.notifications.notify_waitlist_promoted.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_booking_confirmed_promotes_waitlist():
    svc = _service()
    svc.repo.fetch_booking_by_id = AsyncMock(
        return_value={
            "id": BOOKING_ID,
            "event_id": EVENT_ID,
            "project_id": PROJECT_ID,
            "contact_id": CONTACT_ID,
            "unit_id": UNIT_ID,
            "booking_status": CommunityEventBookingStatus.CONFIRMED.value,
            "total_tickets": 2,
        }
    )
    svc.repo.update_booking_fields = AsyncMock()
    svc.repo.adjust_event_aggregates_on_booking = AsyncMock()
    svc.repo.insert_audit_log = AsyncMock()
    svc._promote_waitlist = AsyncMock()

    await svc.cancel_booking(
        contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
        booking_id=BOOKING_ID,
    )

    svc._promote_waitlist.assert_awaited_once_with(event_id=EVENT_ID)


@pytest.mark.asyncio
async def test_cancel_booking_not_found_and_already_cancelled():
    svc = _service()
    svc.repo.fetch_booking_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.cancel_booking(contact_id=CONTACT_ID, unit_id=UNIT_ID, booking_id=BOOKING_ID)

    svc.repo.fetch_booking_by_id = AsyncMock(
        return_value={
            "id": BOOKING_ID,
            "booking_status": CommunityEventBookingStatus.CANCELLED.value,
        }
    )
    with pytest.raises(ConflictException):
        await svc.cancel_booking(contact_id=CONTACT_ID, unit_id=UNIT_ID, booking_id=BOOKING_ID)
