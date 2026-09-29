"""Unit tests for FacilityAvailabilityService."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums import (
    FacilityBookingArchetype,
    FacilityParticipantKind,
)
from apps.user_service.app.schemas.facility_booking import (
    ParticipantInput,
    ReservationDraftRequest,
)
from apps.user_service.app.services.facility_availability_service import (
    FacilityAvailabilityService,
)
from apps.user_service.app.services.facility_booking.defaults import (
    default_booking_config,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
FACILITY_ID = "33333333-3333-3333-3333-333333333333"
CONTACT_ID = "44444444-4444-4444-4444-444444444444"
ORG_ID = "22222222-2222-2222-2222-222222222222"


def _config_row(*, accepting: bool = True) -> dict:
    defaults = default_booking_config(FacilityBookingArchetype.SLOT)
    return {
        "facility_id": FACILITY_ID,
        "facility_name": "Pool",
        "archetype": FacilityBookingArchetype.SLOT.value,
        "slot_minutes": defaults.slot_minutes,
        "default_hours": [item.model_dump() for item in defaults.default_hours],
        "pricing": defaults.pricing.model_dump(),
        "policies": defaults.policies.model_dump(),
        "setup": defaults.setup.model_dump(),
        "description": "",
        "accepting_bookings": accepting,
    }


def _empty_inventory() -> dict:
    return {
        "facility_booking_units": [],
        "facility_schedule_periods": [],
        "facility_slot_blocks": [],
        "facility_closures": [],
        "facility_maintenance_windows": [],
    }


def _service() -> FacilityAvailabilityService:
    svc = FacilityAvailabilityService(
        db_connection=MagicMock(),
        user_context=UserContext(user_id="u1", email="a@b.com", organization_id=ORG_ID),
    )
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.config_repo = MagicMock()
    svc.inventory_repo = MagicMock()
    svc.reservations_repo = MagicMock()
    svc.reservations_repo.list_active_in_range = AsyncMock(return_value=[])
    svc.reservations_repo.participants_by_reservation = AsyncMock(return_value={})
    svc.reservations_repo.list_weekly_pool = AsyncMock(return_value=[])
    svc.config_service = MagicMock()
    svc.config_service.local_now = AsyncMock(return_value=datetime(2026, 9, 24, 10, 0))
    return svc


def _draft_body() -> ReservationDraftRequest:
    return ReservationDraftRequest(
        facility_id=FACILITY_ID,
        local_date=date(2026, 10, 5),
        start_min=600,
        end_min=720,
        participants=[
            ParticipantInput(
                kind=FacilityParticipantKind.RESIDENT, contact_id=CONTACT_ID, name="Ada"
            )
        ],
    )


@pytest.mark.asyncio
async def test_load_snapshot_not_found() -> None:
    svc = _service()
    svc.config_repo.get_config = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.load_snapshot(project_id=PROJECT_ID, facility_id=FACILITY_ID)


@pytest.mark.asyncio
async def test_load_snapshot_success() -> None:
    svc = _service()
    config = _config_row()
    svc.config_repo.get_config = AsyncMock(return_value=config)
    svc.inventory_repo.load_all = AsyncMock(return_value=_empty_inventory())
    snapshot, row = await svc.load_snapshot(project_id=PROJECT_ID, facility_id=FACILITY_ID)
    assert snapshot.id == FACILITY_ID
    assert row["facility_id"] == FACILITY_ID


@pytest.mark.asyncio
async def test_build_context_with_weekly_pool() -> None:
    svc = _service()
    config = _config_row()
    svc.config_repo.get_config = AsyncMock(return_value=config)
    svc.inventory_repo.load_all = AsyncMock(return_value=_empty_inventory())
    ctx, _ = await svc.build_context(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        range_start=date(2026, 10, 5),
        range_end=date(2026, 10, 5),
        host_contact_id=CONTACT_ID,
    )
    assert ctx.facility.id == FACILITY_ID
    svc.reservations_repo.list_weekly_pool.assert_awaited_once()


def test_draft_maps_participants() -> None:
    body = _draft_body()
    draft = FacilityAvailabilityService.draft(body, host_contact_id=CONTACT_ID)
    assert draft.host_contact_id == CONTACT_ID
    assert draft.participants[0].name == "Ada"
    assert draft.end_local_date == body.local_date


@pytest.mark.asyncio
async def test_evaluate_draft_and_quote() -> None:
    svc = _service()
    config = _config_row()
    svc.config_repo.get_config = AsyncMock(return_value=config)
    svc.inventory_repo.load_all = AsyncMock(return_value=_empty_inventory())
    body = _draft_body()
    result = await svc.evaluate_draft(
        project_id=PROJECT_ID,
        body=body,
        host_contact_id=CONTACT_ID,
    )
    assert "ok" in result
    assert "quote" in result

    with patch.object(
        svc,
        "evaluate_draft",
        return_value={"ok": True, "errors": [], "quote": {"total": 0, "lines": []}},
    ):
        quote = await svc.quote(
            project_id=PROJECT_ID,
            body=body,
            host_contact_id=CONTACT_ID,
        )
    assert quote["total"] == 0


@pytest.mark.asyncio
async def test_evaluate_draft_staff_override() -> None:
    svc = _service()
    config = _config_row()
    svc.config_repo.get_config = AsyncMock(return_value=config)
    svc.inventory_repo.load_all = AsyncMock(return_value=_empty_inventory())
    body = _draft_body()
    with patch(
        "apps.user_service.app.services.facility_availability_service.availability.without_codes",
        side_effect=lambda validation, _codes: validation,
    ):
        result = await svc.evaluate_draft(
            project_id=PROJECT_ID,
            body=body,
            host_contact_id=CONTACT_ID,
            staff_override=True,
        )
    assert "ok" in result


@pytest.mark.asyncio
async def test_quote_raises_when_invalid() -> None:
    svc = _service()
    body = _draft_body()
    with patch.object(
        svc,
        "evaluate_draft",
        return_value={
            "ok": False,
            "errors": [{"code": "closed", "message": "Closed"}],
            "quote": {},
        },
    ):
        with pytest.raises(ValidationException):
            await svc.quote(project_id=PROJECT_ID, body=body, host_contact_id=CONTACT_ID)


@pytest.mark.asyncio
async def test_availability_endpoints() -> None:
    svc = _service()
    config = _config_row()
    svc.config_repo.get_config = AsyncMock(return_value=config)
    svc.inventory_repo.load_all = AsyncMock(return_value=_empty_inventory())
    day = await svc.availability_day(
        project_id=PROJECT_ID, facility_id=FACILITY_ID, local_date=date(2026, 10, 5)
    )
    assert "date" in day or "slots" in day or isinstance(day, dict)

    month = await svc.availability_month(
        project_id=PROJECT_ID, facility_id=FACILITY_ID, start=date(2026, 10, 1)
    )
    assert isinstance(month, list)

    summary = await svc.availability_month_summary(
        project_id=PROJECT_ID, facility_id=FACILITY_ID, start=date(2026, 10, 1)
    )
    assert isinstance(summary, list)

    nxt = await svc.next_availability(project_id=PROJECT_ID, facility_id=FACILITY_ID)
    assert nxt is None or isinstance(nxt, dict)

    rooms = await svc.room_availability(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        check_in=date(2026, 10, 10),
        check_out=date(2026, 10, 12),
    )
    assert isinstance(rooms, list)

    usage = await svc.weekly_usage(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        contact_id=CONTACT_ID,
        local_date=date(2026, 10, 5),
    )
    assert "usage" in usage
    assert "cap" in usage
