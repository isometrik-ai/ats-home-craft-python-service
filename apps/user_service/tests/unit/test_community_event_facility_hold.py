"""Unit tests for community event facility slot holds."""

from __future__ import annotations

from datetime import date, time
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.facility_booking_inventory import (
    CreateSlotBlockRequest,
)
from apps.user_service.app.services.community_event_facility_hold import (
    SLOT_BLOCK_CATEGORY,
    CommunityEventFacilityHoldService,
    event_hold_minutes,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ConflictException, ValidationException

ORG_ID = "org-1"
PROJECT_ID = "11111111-1111-1111-1111-111111111111"
EVENT_ID = "22222222-2222-2222-2222-222222222222"
FACILITY_ID = "33333333-3333-3333-3333-333333333333"


def _event(**overrides) -> dict:
    row = {
        "id": EVENT_ID,
        "title": "Holi night",
        "facility_id": FACILITY_ID,
        "start_date": date(2026, 10, 22),
        "end_date": date(2026, 10, 22),
        "start_time": time(18, 0),
        "end_time": time(21, 0),
    }
    row.update(overrides)
    return row


def _service() -> CommunityEventFacilityHoldService:
    svc = CommunityEventFacilityHoldService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="user-1",
            email="admin@example.com",
            organization_id=ORG_ID,
        ),
    )
    svc.facilities_repo = MagicMock()
    svc.inventory_repo = MagicMock()
    svc.reservations_repo = MagicMock()
    svc.config = MagicMock()
    return svc


def test_event_hold_minutes_defaults_to_full_day() -> None:
    assert event_hold_minutes(start_time=None, end_time=None) == (0, 1440)


def test_event_hold_minutes_rejects_inverted_window() -> None:
    with pytest.raises(ValidationException):
        event_hold_minutes(start_time=time(21, 0), end_time=time(18, 0))


@pytest.mark.asyncio
async def test_ensure_skips_non_bookable_venue() -> None:
    svc = _service()
    svc.facilities_repo.get_facility = AsyncMock(return_value={"is_bookable": False})
    svc.inventory_repo.get_slot_block_for_event = AsyncMock(return_value=None)

    assert await svc.ensure(project_id=PROJECT_ID, event=_event()) is None
    svc.config.create_slot_block.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_releases_when_facility_missing() -> None:
    svc = _service()
    svc.inventory_repo.get_slot_block_for_event = AsyncMock(
        return_value={"id": "block-1", "facility_id": FACILITY_ID}
    )
    svc.config.delete_inventory = AsyncMock()

    assert await svc.ensure(project_id=PROJECT_ID, event=_event(facility_id=None)) is None
    svc.config.delete_inventory.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_creates_block_through_config_service() -> None:
    svc = _service()
    svc.facilities_repo.get_facility = AsyncMock(return_value={"is_bookable": True})
    svc.reservations_repo.list_active_in_range = AsyncMock(return_value=[])
    svc.inventory_repo.get_slot_block_for_event = AsyncMock(return_value=None)
    svc.config.create_slot_block = AsyncMock(return_value={"id": "block-1"})

    row = await svc.ensure(project_id=PROJECT_ID, event=_event())

    assert row["id"] == "block-1"
    kwargs = svc.config.create_slot_block.await_args.kwargs
    assert kwargs["project_id"] == PROJECT_ID
    assert kwargs["facility_id"] == FACILITY_ID
    body = kwargs["body"]
    assert isinstance(body, CreateSlotBlockRequest)
    assert body.from_min == 18 * 60
    assert body.to_min == 21 * 60
    assert body.community_event_id == EVENT_ID
    assert body.category == SLOT_BLOCK_CATEGORY
    assert body.unit_id is None
    assert body.reason.startswith("Community event:")


@pytest.mark.asyncio
async def test_ensure_replaces_existing_block() -> None:
    svc = _service()
    svc.facilities_repo.get_facility = AsyncMock(return_value={"is_bookable": True})
    svc.reservations_repo.list_active_in_range = AsyncMock(return_value=[])
    svc.inventory_repo.get_slot_block_for_event = AsyncMock(
        return_value={"id": "block-1", "facility_id": FACILITY_ID}
    )
    svc.config.delete_inventory = AsyncMock()
    svc.config.create_slot_block = AsyncMock(return_value={"id": "block-2"})

    await svc.ensure(project_id=PROJECT_ID, event=_event(title="Diwali"))

    svc.config.delete_inventory.assert_awaited_once()
    body = svc.config.create_slot_block.await_args.kwargs["body"]
    assert "Diwali" in body.reason


@pytest.mark.asyncio
async def test_ensure_conflicts_when_reservation_overlaps() -> None:
    svc = _service()
    svc.facilities_repo.get_facility = AsyncMock(return_value={"is_bookable": True})
    svc.reservations_repo.list_active_in_range = AsyncMock(
        return_value=[{"start_min": 18 * 60, "end_min": 19 * 60}]
    )

    with pytest.raises(ConflictException):
        await svc.ensure(project_id=PROJECT_ID, event=_event())
    svc.config.create_slot_block.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_allows_reservation_outside_event_hours() -> None:
    svc = _service()
    svc.facilities_repo.get_facility = AsyncMock(return_value={"is_bookable": True})
    svc.reservations_repo.list_active_in_range = AsyncMock(
        return_value=[{"start_min": 10 * 60, "end_min": 11 * 60}]
    )
    svc.inventory_repo.get_slot_block_for_event = AsyncMock(return_value=None)
    svc.config.create_slot_block = AsyncMock(return_value={"id": "block-1"})

    await svc.ensure(project_id=PROJECT_ID, event=_event())
    svc.config.create_slot_block.assert_awaited_once()
