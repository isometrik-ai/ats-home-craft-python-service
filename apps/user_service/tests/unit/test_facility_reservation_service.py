"""Unit tests for FacilityReservationService transitions."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import FacilityReservationStatus
from apps.user_service.app.schemas.facility_booking import RejectReservationRequest
from apps.user_service.app.services.facility_reservation_service import (
    FacilityReservationService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ValidationException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
RESERVATION_ID = "22222222-2222-2222-2222-222222222222"


def _service() -> FacilityReservationService:
    svc = FacilityReservationService(
        db_connection=MagicMock(),
        user_context=UserContext(user_id="user-1", email="a@b.com", organization_id="org-1"),
    )
    svc.reservations_repo = MagicMock()
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
    svc.availability = MagicMock()
    svc.availability.load_snapshot = AsyncMock(
        return_value=(
            MagicMock(policies=MagicMock(reschedule_cutoff_hours=24, no_show_fee_percent=50)),
            {},
        )
    )
    return svc


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
    row = {
        "id": RESERVATION_ID,
        "status": FacilityReservationStatus.PENDING_APPROVAL.value,
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
        "starts_at": datetime(2026, 10, 1, 9, 0),
        "ends_at": datetime(2026, 10, 1, 12, 0),
        "quote": {"lines": [], "total": 0, "deposit": 0, "due_now": 0, "due_later": 0},
        "rescheduled_from_id": None,
        "rescheduled_to_id": None,
        "reject_reason": "Full",
        "cancel_info": None,
        "notes": None,
        "booked_by_actor": "resident",
        "created_at": datetime(2026, 9, 24, 10, 0),
        "approved_at": None,
        "checked_in_at": None,
        "completed_at": None,
        "cancelled_at": None,
    }
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
