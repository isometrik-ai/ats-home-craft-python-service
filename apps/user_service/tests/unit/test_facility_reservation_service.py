"""Unit tests for FacilityReservationService transitions."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from apps.user_service.app.schemas.enums import (
    FacilityBookingArchetype,
    FacilityParticipantKind,
    FacilityReservationListTab,
    FacilityReservationStatus,
)
from apps.user_service.app.schemas.facility_booking import (
    CancelReservationRequest,
    CreateResidentReservationRequest,
    CreateStaffReservationRequest,
    ParticipantInput,
    PriceQuote,
    RejectReservationRequest,
    RescheduleReservationRequest,
    ReservationNoteRequest,
)
from apps.user_service.app.services.facility_availability_service import (
    FacilityAvailabilityService,
)
from apps.user_service.app.services.facility_booking.defaults import (
    default_booking_config,
)
from apps.user_service.app.services.facility_booking.snapshot import snapshot_from_rows
from apps.user_service.app.services.facility_booking.types import (
    Validation,
    ValidationIssue,
)
from apps.user_service.app.services.facility_reservation_service import (
    FacilityReservationService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
RESERVATION_ID = "22222222-2222-2222-2222-222222222222"


def _service() -> FacilityReservationService:
    svc = FacilityReservationService(
        db_connection=MagicMock(),
        user_context=UserContext(user_id="user-1", email="a@b.com", organization_id="org-1"),
    )
    svc.reservations_repo = MagicMock()
    svc.reservations_repo.list_events = AsyncMock(return_value=[])
    svc.config_repo = MagicMock()
    svc.inventory_repo = MagicMock()
    svc.config_service = MagicMock()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.config_service.local_now = AsyncMock(return_value=datetime(2026, 9, 24, 10, 0))
    svc.config_service.timezone_for = AsyncMock(return_value="UTC")
    svc.ledger = MagicMock()
    svc.ledger.net_paid_for = AsyncMock(return_value=0)
    svc.ledger.post = AsyncMock()
    svc.notifier = MagicMock()
    svc.notifier.confirmed = AsyncMock()
    svc.notifier.submitted = AsyncMock()
    svc.notifier.approval_requested = AsyncMock()
    svc.notifier.approved = AsyncMock()
    svc.notifier.rejected = AsyncMock()
    svc.notifier.checked_in = AsyncMock()
    svc.notifier.cancelled = AsyncMock()
    svc.notifier.rescheduled = AsyncMock()
    svc.notifier.no_show = AsyncMock()
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_project_membership = AsyncMock(return_value=True)
    svc.availability = MagicMock()
    svc.availability.load_snapshot = AsyncMock(
        return_value=(
            MagicMock(policies=MagicMock(reschedule_cutoff_hours=24, no_show_fee_percent=50)),
            {},
        )
    )
    return svc


def _reservation_row(*, status: str = FacilityReservationStatus.PENDING_APPROVAL.value) -> dict:
    return {
        "id": RESERVATION_ID,
        "status": status,
        "host_contact_id": "c1",
        "facility_id": "f1",
        "facility_name": "Hall",
        "archetype": "day_range",
        "unit_id": None,
        "unit_name": None,
        "host_name": "Ada",
        "host_unit_id": None,
        "local_date": date(2026, 10, 1),
        "end_local_date": date(2026, 10, 1),
        "start_min": 540,
        "end_min": 720,
        "starts_at": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        "ends_at": datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        "quote": {"lines": [], "total": 0, "deposit": 0, "due_now": 0, "due_later": 0},
        "rescheduled_from_id": None,
        "rescheduled_to_id": None,
        "reject_reason": None,
        "cancel_info": None,
        "notes": None,
        "booked_by_actor": "resident",
        "created_at": datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
        "approved_at": None,
        "checked_in_at": None,
        "completed_at": None,
        "cancelled_at": None,
    }


def _mock_get_reservation_flow(svc: FacilityReservationService, row: dict) -> None:
    svc.reservations_repo.get_reservation = AsyncMock(return_value=row)
    svc.reservations_repo.participants_by_reservation = AsyncMock(return_value={})
    svc.reservations_repo.list_events = AsyncMock(return_value=[])


@pytest.mark.asyncio
async def test_approve_rejects_invalid_status():
    svc = _service()
    svc.reservations_repo.get_reservation = AsyncMock(
        return_value={
            "id": RESERVATION_ID,
            "status": FacilityReservationStatus.CONFIRMED.value,
            "host_contact_id": "c1",
            "facility_id": "f1",
        }
    )

    with pytest.raises(ValidationException):
        await svc.approve(project_id=PROJECT_ID, reservation_id=RESERVATION_ID)


@pytest.mark.asyncio
async def test_reject_writes_reason():
    svc = _service()
    row = _reservation_row()
    svc.reservations_repo.get_reservation = AsyncMock(return_value=row)
    svc.reservations_repo.update_reservation = AsyncMock(return_value=True)
    svc.reservations_repo.insert_event = AsyncMock()
    svc.reservations_repo.participants_by_reservation = AsyncMock(return_value={})
    svc.reservations_repo.list_events = AsyncMock(return_value=[])
    result = await svc.reject(
        project_id=PROJECT_ID,
        reservation_id=RESERVATION_ID,
        body=RejectReservationRequest(reason="Full"),
    )

    update = svc.reservations_repo.update_reservation.await_args.kwargs["update_data"]
    assert update["status"] == FacilityReservationStatus.REJECTED.value
    assert update["reject_reason"] == "Full"
    assert result["id"] == RESERVATION_ID
    svc.notifier.rejected.assert_awaited_once()


def test_helpers_zone_aware_and_labels():
    svc = _service()
    zone = ZoneInfo("UTC")
    aware = svc._aware(date(2026, 10, 1), 600, zone)
    assert aware.hour == 10

    snapshot = SimpleNamespace(name="Pool", units=[])
    assert svc._facility_label(snapshot, None) == "Pool"

    snapshot.units = [SimpleNamespace(id="u1", name="Lane 1")]
    assert svc._facility_label(snapshot, "u1") == "Pool"

    snapshot.units.append(SimpleNamespace(id="u2", name="Lane 2"))
    assert svc._facility_label(snapshot, "u1") == "Pool · Lane 1"

    with pytest.raises(ValidationException):
        svc._raise_if_invalid(Validation(ok=False, errors=[ValidationIssue("x", "bad")]))


@pytest.mark.asyncio
async def test_zone_falls_back_to_utc():
    svc = _service()
    svc.config_service.timezone_for = AsyncMock(return_value="Not/A_Real_Zone")
    zone = await svc._zone(PROJECT_ID)
    assert str(zone) == "UTC"


@pytest.mark.asyncio
async def test_post_ledger_skips_zero_amount():
    svc = _service()
    await svc._post_ledger(
        project_id=PROJECT_ID,
        contact_id="c1",
        reservation_id=RESERVATION_ID,
        entry_type="charge",
        description="Free",
        amount=0,
    )
    svc.ledger.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_reservation_not_found_and_forbidden():
    svc = _service()
    svc.reservations_repo.get_reservation = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.get_reservation(project_id=PROJECT_ID, reservation_id=RESERVATION_ID)

    row = _reservation_row()
    _mock_get_reservation_flow(svc, row)
    with pytest.raises(ForbiddenException):
        await svc.get_reservation(
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            host_contact_id="other",
        )


@pytest.mark.asyncio
async def test_list_reservations_and_list_mine():
    svc = _service()
    row = _reservation_row(status=FacilityReservationStatus.CONFIRMED.value)
    svc.reservations_repo.list_reservations = AsyncMock(return_value=([row], 1))
    svc.reservations_repo.participants_by_reservation = AsyncMock(return_value={RESERVATION_ID: []})
    items, total = await svc.list_reservations(project_id=PROJECT_ID, include_events=True)
    assert total == 1
    assert items[0]["id"] == RESERVATION_ID

    svc.reservations_repo.list_reservations = AsyncMock(return_value=([], 0))
    upcoming, _ = await svc.list_mine(
        project_id=PROJECT_ID,
        contact_id="c1",
        tab=FacilityReservationListTab.UPCOMING,
        facility_id=None,
        page=1,
        page_size=20,
    )
    assert upcoming == []


@pytest.mark.asyncio
async def test_approve_posts_deposit_and_notifies():
    svc = _service()
    row = _reservation_row()
    row["quote"] = {"lines": [], "total": 500, "deposit": 200, "due_now": 200, "due_later": 300}
    _mock_get_reservation_flow(svc, row)
    svc.reservations_repo.update_reservation = AsyncMock(return_value=True)
    svc.reservations_repo.insert_event = AsyncMock()

    await svc.approve(project_id=PROJECT_ID, reservation_id=RESERVATION_ID)

    svc.ledger.post.assert_awaited_once()
    svc.notifier.approved.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_in_and_complete():
    svc = _service()
    row = _reservation_row(status=FacilityReservationStatus.CONFIRMED.value)
    row["quote"] = {"lines": [], "total": 500, "deposit": 200, "due_now": 200, "due_later": 100}
    _mock_get_reservation_flow(svc, row)
    svc.reservations_repo.update_reservation = AsyncMock(return_value=True)
    svc.reservations_repo.insert_event = AsyncMock()

    await svc.check_in(project_id=PROJECT_ID, reservation_id=RESERVATION_ID)
    svc.ledger.post.assert_awaited_once()
    svc.notifier.checked_in.assert_awaited_once()

    svc.ledger.post.reset_mock()
    row["status"] = FacilityReservationStatus.CHECKED_IN.value
    _mock_get_reservation_flow(svc, row)
    await svc.complete(project_id=PROJECT_ID, reservation_id=RESERVATION_ID)


@pytest.mark.asyncio
async def test_mark_no_show_refund_and_forfeit():
    svc = _service()
    row = _reservation_row(status=FacilityReservationStatus.CONFIRMED.value)
    _mock_get_reservation_flow(svc, row)
    svc.reservations_repo.update_reservation = AsyncMock(return_value=True)
    svc.reservations_repo.insert_event = AsyncMock()
    svc.ledger.net_paid_for = AsyncMock(return_value=1000)

    await svc.mark_no_show(project_id=PROJECT_ID, reservation_id=RESERVATION_ID)

    assert svc.ledger.post.await_count == 2
    svc.notifier.no_show.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_quote_and_resident_cancel():
    svc = _service()
    row = _reservation_row(status=FacilityReservationStatus.CONFIRMED.value)
    svc.reservations_repo.get_reservation = AsyncMock(return_value=row)
    svc.ledger.net_paid_for = AsyncMock(return_value=0)
    defaults = default_booking_config(FacilityBookingArchetype.SLOT)
    config = {
        "facility_id": "f1",
        "facility_name": "Court",
        "archetype": FacilityBookingArchetype.SLOT.value,
        "slot_minutes": 60,
        "default_hours": [h.model_dump() for h in defaults.default_hours],
        "pricing": defaults.pricing.model_dump(),
        "policies": defaults.policies.model_dump(),
        "setup": defaults.setup.model_dump(),
        "accepting_bookings": True,
    }
    snapshot = snapshot_from_rows(
        config,
        {
            "facility_booking_units": [],
            "facility_schedule_periods": [],
            "facility_slot_blocks": [],
            "facility_closures": [],
            "facility_maintenance_windows": [],
        },
    )
    svc.availability.load_snapshot = AsyncMock(return_value=(snapshot, config))

    quote = await svc.cancel_quote(project_id=PROJECT_ID, reservation_id=RESERVATION_ID)
    assert "fee" in quote

    _mock_get_reservation_flow(svc, row)
    svc.reservations_repo.update_reservation = AsyncMock(return_value=True)
    svc.reservations_repo.insert_event = AsyncMock()
    with patch(
        "apps.user_service.app.services.facility_reservation_service.pricing.cancel_quote",
        return_value=MagicMock(
            model_dump=MagicMock(
                return_value={
                    "fee": 0,
                    "refund": 0,
                    "paid": 0,
                    "free": True,
                    "hours_before": 48,
                    "tier": None,
                }
            )
        ),
    ):
        await svc.cancel(
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            body=CancelReservationRequest(reason="Plans changed"),
            actor_type="resident",
            host_contact_id="c1",
        )
    svc.notifier.cancelled.assert_awaited_once()


@pytest.mark.asyncio
async def test_staff_cancel_full_refund():
    svc = _service()
    row = _reservation_row(status=FacilityReservationStatus.CONFIRMED.value)
    _mock_get_reservation_flow(svc, row)
    svc.reservations_repo.update_reservation = AsyncMock(return_value=True)
    svc.reservations_repo.insert_event = AsyncMock()
    svc.ledger.net_paid_for = AsyncMock(return_value=500)

    await svc.cancel(
        project_id=PROJECT_ID,
        reservation_id=RESERVATION_ID,
        body=CancelReservationRequest(reason="Weather"),
        actor_type="staff",
    )
    svc.ledger.post.assert_awaited_once()
    assert "full refund" in svc.ledger.post.await_args.kwargs["description"].lower()


@pytest.mark.asyncio
async def test_create_staff_rejects_unknown_host():
    svc = _service()
    svc.contact_units_repo.contact_has_active_project_membership = AsyncMock(return_value=False)
    body = CreateStaffReservationRequest(
        facility_id="f1",
        host_contact_id="c9",
        local_date=date(2026, 10, 1),
        start_min=600,
        end_min=720,
        participants=[],
    )
    with pytest.raises(ValidationException):
        await svc.create_staff(project_id=PROJECT_ID, body=body)


@pytest.mark.asyncio
async def test_create_resident_confirmed_flow():
    svc = _service()
    defaults = default_booking_config(FacilityBookingArchetype.SLOT)
    config = {
        "facility_id": "f1",
        "facility_name": "Court",
        "archetype": FacilityBookingArchetype.SLOT.value,
        "slot_minutes": 60,
        "default_hours": [h.model_dump() for h in defaults.default_hours],
        "pricing": defaults.pricing.model_dump(),
        "policies": {**defaults.policies.model_dump(), "requires_approval": False},
        "setup": defaults.setup.model_dump(),
        "accepting_bookings": True,
    }
    snapshot = snapshot_from_rows(
        config,
        {
            "facility_booking_units": [],
            "facility_schedule_periods": [],
            "facility_slot_blocks": [],
            "facility_closures": [],
            "facility_maintenance_windows": [],
        },
    )
    body = CreateResidentReservationRequest(
        facility_id="f1",
        local_date=date(2026, 10, 5),
        start_min=600,
        end_min=720,
        participants=[
            ParticipantInput(kind=FacilityParticipantKind.RESIDENT, contact_id="c1", name="Ada")
        ],
    )
    draft = FacilityAvailabilityService.draft(body, host_contact_id="c1")
    svc.availability.draft = MagicMock(return_value=draft)
    svc.availability.build_context = AsyncMock(return_value=(MagicMock(facility=snapshot), config))
    svc.reservations_repo.lock_facility = AsyncMock()
    svc.reservations_repo.insert_reservation = AsyncMock(
        return_value={"id": "new-res", "facility_id": "f1"}
    )
    svc.reservations_repo.insert_participants = AsyncMock()
    svc.reservations_repo.insert_event = AsyncMock()
    _mock_get_reservation_flow(
        svc, _reservation_row(status=FacilityReservationStatus.CONFIRMED.value)
    )

    with patch(
        "apps.user_service.app.services.facility_reservation_service.ensure_host_unit",
        new_callable=AsyncMock,
    ):
        with patch(
            "apps.user_service.app.services.facility_reservation_service.availability.validate_draft",
            return_value=Validation(ok=True, errors=[]),
        ):
            with patch(
                "apps.user_service.app.services.facility_reservation_service.pricing.quote_booking",
                return_value=PriceQuote(total=400, deposit=0, due_now=400, due_later=0),
            ):
                with patch(
                    "apps.user_service.app.services.facility_reservation_service.pricing.is_billed_later",
                    return_value=False,
                ):
                    result = await svc.create_resident(
                        project_id=PROJECT_ID, contact_id="c1", body=body
                    )
    assert result["id"] == RESERVATION_ID
    svc.notifier.confirmed.assert_awaited_once()
    svc.ledger.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_note_appends_timeline():
    svc = _service()
    _mock_get_reservation_flow(svc, _reservation_row())
    svc.reservations_repo.insert_event = AsyncMock()
    await svc.add_note(
        project_id=PROJECT_ID,
        reservation_id=RESERVATION_ID,
        body=ReservationNoteRequest(message="VIP guest"),
    )
    assert svc.reservations_repo.insert_event.await_count == 1


@pytest.mark.asyncio
async def test_get_reservation_success():
    svc = _service()
    _mock_get_reservation_flow(svc, _reservation_row())
    result = await svc.get_reservation(project_id=PROJECT_ID, reservation_id=RESERVATION_ID)
    assert result["host_name"] == "Ada"


@pytest.mark.asyncio
async def test_list_mine_past_tab():
    svc = _service()
    svc.reservations_repo.list_reservations = AsyncMock(return_value=([], 0))
    svc.reservations_repo.participants_by_reservation = AsyncMock(return_value={})
    await svc.list_mine(
        project_id=PROJECT_ID,
        contact_id="c1",
        tab=FacilityReservationListTab.PAST,
        facility_id=None,
        page=1,
        page_size=10,
    )
    kwargs = svc.reservations_repo.list_reservations.await_args.kwargs
    assert kwargs["descending"] is True
    assert kwargs["exclude_statuses"] == [FacilityReservationStatus.RESCHEDULED.value]


@pytest.mark.asyncio
async def test_create_rejects_not_bookable_and_invalid_draft():
    svc = _service()
    defaults = default_booking_config(FacilityBookingArchetype.SLOT)
    config = {
        "facility_id": "f1",
        "facility_name": "Court",
        "archetype": FacilityBookingArchetype.SLOT.value,
        "slot_minutes": 60,
        "default_hours": [h.model_dump() for h in defaults.default_hours],
        "pricing": defaults.pricing.model_dump(),
        "policies": defaults.policies.model_dump(),
        "setup": defaults.setup.model_dump(),
        "accepting_bookings": False,
    }
    snapshot = snapshot_from_rows(
        config,
        {
            "facility_booking_units": [],
            "facility_schedule_periods": [],
            "facility_slot_blocks": [],
            "facility_closures": [],
            "facility_maintenance_windows": [],
        },
    )
    body = CreateResidentReservationRequest(
        facility_id="f1",
        local_date=date(2026, 10, 5),
        start_min=600,
        end_min=720,
        participants=[
            ParticipantInput(kind=FacilityParticipantKind.RESIDENT, contact_id="c1", name="Ada")
        ],
    )
    draft = FacilityAvailabilityService.draft(body, host_contact_id="c1")
    svc.availability.draft = MagicMock(return_value=draft)
    svc.availability.build_context = AsyncMock(return_value=(MagicMock(facility=snapshot), config))
    svc.reservations_repo.lock_facility = AsyncMock()
    with patch(
        "apps.user_service.app.services.facility_reservation_service.ensure_host_unit",
        new_callable=AsyncMock,
    ):
        with pytest.raises(ValidationException):
            await svc.create_resident(project_id=PROJECT_ID, contact_id="c1", body=body)

    config["accepting_bookings"] = True
    snapshot = snapshot_from_rows(
        config,
        {
            "facility_booking_units": [],
            "facility_schedule_periods": [],
            "facility_slot_blocks": [],
            "facility_closures": [],
            "facility_maintenance_windows": [],
        },
    )
    svc.availability.build_context = AsyncMock(return_value=(MagicMock(facility=snapshot), config))
    with patch(
        "apps.user_service.app.services.facility_reservation_service.ensure_host_unit",
        new_callable=AsyncMock,
    ):
        with patch(
            "apps.user_service.app.services.facility_reservation_service.availability.validate_draft",
            return_value=Validation(ok=False, errors=[ValidationIssue("bad", "Invalid")]),
        ):
            with pytest.raises(ValidationException):
                await svc.create_resident(project_id=PROJECT_ID, contact_id="c1", body=body)


@pytest.mark.asyncio
async def test_create_pending_triggers_submitted_and_approval():
    svc = _service()
    defaults = default_booking_config(FacilityBookingArchetype.DURATION)
    config = {
        "facility_id": "f1",
        "facility_name": "Hall",
        "archetype": FacilityBookingArchetype.DURATION.value,
        "slot_minutes": 30,
        "default_hours": [h.model_dump() for h in defaults.default_hours],
        "pricing": defaults.pricing.model_dump(),
        "policies": defaults.policies.model_dump(),
        "setup": defaults.setup.model_dump(),
        "accepting_bookings": True,
    }
    snapshot = snapshot_from_rows(
        config,
        {
            "facility_booking_units": [],
            "facility_schedule_periods": [],
            "facility_slot_blocks": [],
            "facility_closures": [],
            "facility_maintenance_windows": [],
        },
    )
    body = CreateResidentReservationRequest(
        facility_id="f1",
        local_date=date(2026, 10, 5),
        start_min=600,
        end_min=900,
        participants=[
            ParticipantInput(kind=FacilityParticipantKind.RESIDENT, contact_id="c1", name="Ada")
        ],
    )
    draft = FacilityAvailabilityService.draft(body, host_contact_id="c1")
    svc.availability.draft = MagicMock(return_value=draft)
    svc.availability.build_context = AsyncMock(return_value=(MagicMock(facility=snapshot), config))
    svc.reservations_repo.lock_facility = AsyncMock()
    svc.reservations_repo.insert_reservation = AsyncMock(return_value={"id": "new-res"})
    svc.reservations_repo.insert_participants = AsyncMock()
    svc.reservations_repo.insert_event = AsyncMock()
    pending_row = _reservation_row(status=FacilityReservationStatus.PENDING_APPROVAL.value)
    _mock_get_reservation_flow(svc, pending_row)

    with patch(
        "apps.user_service.app.services.facility_reservation_service.ensure_host_unit",
        new_callable=AsyncMock,
    ):
        with patch(
            "apps.user_service.app.services.facility_reservation_service.availability.validate_draft",
            return_value=Validation(ok=True, errors=[]),
        ):
            with patch(
                "apps.user_service.app.services.facility_reservation_service.pricing.quote_booking",
                return_value=PriceQuote(total=0),
            ):
                await svc.create_resident(project_id=PROJECT_ID, contact_id="c1", body=body)

    svc.notifier.submitted.assert_awaited_once()
    svc.notifier.approval_requested.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_with_fee_posts_cancellation_ledger():
    svc = _service()
    row = _reservation_row(status=FacilityReservationStatus.CONFIRMED.value)
    _mock_get_reservation_flow(svc, row)
    svc.reservations_repo.update_reservation = AsyncMock(return_value=True)
    svc.reservations_repo.insert_event = AsyncMock()
    svc.ledger.net_paid_for = AsyncMock(return_value=500)
    cancel_payload = {
        "fee": 100,
        "refund": 400,
        "paid": 500,
        "free": False,
        "hours_before": 1,
        "tier": {"fee_percent": 20},
    }
    with patch.object(svc, "cancel_quote", return_value=cancel_payload):
        await svc.cancel(
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            body=CancelReservationRequest(reason="Late"),
            actor_type="resident",
            host_contact_id="c1",
        )
    assert svc.ledger.post.await_count == 2


@pytest.mark.asyncio
async def test_reschedule_creates_linked_reservation():
    svc = _service()
    current = _reservation_row(status=FacilityReservationStatus.CONFIRMED.value)
    svc.reservations_repo.get_reservation = AsyncMock(return_value=current)
    svc.reservations_repo.participants_by_reservation = AsyncMock(
        return_value={RESERVATION_ID: [{"kind": "resident", "contact_id": "c1", "name": "Ada"}]}
    )
    defaults = default_booking_config(FacilityBookingArchetype.SLOT)
    config = {
        "facility_id": "f1",
        "facility_name": "Court",
        "archetype": FacilityBookingArchetype.SLOT.value,
        "slot_minutes": 60,
        "default_hours": [h.model_dump() for h in defaults.default_hours],
        "pricing": defaults.pricing.model_dump(),
        "policies": defaults.policies.model_dump(),
        "setup": defaults.setup.model_dump(),
        "accepting_bookings": True,
    }
    snapshot = snapshot_from_rows(
        config,
        {
            "facility_booking_units": [],
            "facility_schedule_periods": [],
            "facility_slot_blocks": [],
            "facility_closures": [],
            "facility_maintenance_windows": [],
        },
    )
    svc.availability.load_snapshot = AsyncMock(return_value=(snapshot, config))
    created = _reservation_row(status=FacilityReservationStatus.CONFIRMED.value)
    created["id"] = "new-id"
    with patch.object(svc, "_create", return_value=created):
        svc.reservations_repo.update_reservation = AsyncMock()
        svc.reservations_repo.insert_event = AsyncMock()
        svc.ledger.net_paid_for = AsyncMock(return_value=0)
        result = await svc.reschedule(
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            body=RescheduleReservationRequest(
                local_date=date(2026, 10, 6), start_min=600, end_min=720
            ),
            actor_type="staff",
            staff_override=True,
        )
    assert result["id"] == "new-id"
    svc.notifier.rescheduled.assert_awaited_once()


@pytest.mark.asyncio
async def test_transition_not_found_and_forbidden():
    svc = _service()
    svc.reservations_repo.get_reservation = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.complete(project_id=PROJECT_ID, reservation_id=RESERVATION_ID)

    row = _reservation_row(status=FacilityReservationStatus.CONFIRMED.value)
    svc.reservations_repo.get_reservation = AsyncMock(return_value=row)
    with pytest.raises(ForbiddenException):
        await svc.cancel(
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            body=CancelReservationRequest(),
            actor_type="resident",
            host_contact_id="other",
        )
