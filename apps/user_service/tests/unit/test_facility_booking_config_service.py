"""Unit tests for FacilityBookingConfigService inventory operations."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from asyncpg import CheckViolationError, ForeignKeyViolationError, UniqueViolationError

from apps.user_service.app.schemas.facility_booking_config import DayHours
from apps.user_service.app.schemas.facility_booking_inventory import (
    CreateClosureRequest,
    CreateMaintenanceWindowRequest,
    CreateSchedulePeriodRequest,
    CreateSlotBlockRequest,
)
from apps.user_service.app.services.facility_booking_config_service import (
    FacilityBookingConfigService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ConflictException, ValidationException

PROJECT_ID = "22222222-2222-2222-2222-222222222222"
FACILITY_ID = "33333333-3333-3333-3333-333333333333"
USER_ID = "44444444-4444-4444-4444-444444444444"


def _service() -> FacilityBookingConfigService:
    svc = FacilityBookingConfigService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id=USER_ID,
            email="staff@example.com",
            organization_id="11111111-1111-1111-1111-111111111111",
        ),
    )
    svc.inventory_repo = MagicMock()
    svc.config_repo = MagicMock()
    svc.config_repo.get_config = AsyncMock(return_value={"id": "cfg-1", "version": 1})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    return svc


def _week_hours() -> list[DayHours]:
    return [DayHours(open=360, close=1320, closed=False) for _ in range(7)]


@pytest.mark.asyncio
async def test_create_closure_persists_actor_and_payload() -> None:
    """Closure create forwards payload and creator to inventory insert."""
    svc = _service()
    svc.inventory_repo.insert = AsyncMock(
        return_value={
            "id": "closure-1",
            "facility_id": FACILITY_ID,
            "closed_on": "2026-12-25",
            "reason": "Christmas Holiday Closure",
        }
    )

    row = await svc.create_closure(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        body=CreateClosureRequest(closed_on=date(2026, 12, 25), reason="Christmas Holiday Closure"),
    )

    assert row["id"] == "closure-1"
    svc.inventory_repo.insert.assert_awaited_once()
    _table, kwargs = (
        svc.inventory_repo.insert.await_args.args[0],
        svc.inventory_repo.insert.await_args.kwargs,
    )
    assert _table == "facility_closures"
    assert kwargs["data"]["created_by_user_id"] == USER_ID
    assert kwargs["data"]["reason"] == "Christmas Holiday Closure"


@pytest.mark.asyncio
async def test_create_schedule_persists_hours_and_actor() -> None:
    """Schedule create includes serialized week hours."""
    svc = _service()
    hours = _week_hours()
    serialized_hours = [hour.model_dump(mode="json") for hour in hours]
    svc.inventory_repo.insert = AsyncMock(
        return_value={
            "id": "schedule-1",
            "facility_id": FACILITY_ID,
            "name": "Winter Peak Schedule",
            "starts_on": "2026-11-01",
            "ends_on": "2026-11-30",
            "hours": serialized_hours,
        }
    )

    await svc.create_schedule(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        body=CreateSchedulePeriodRequest(
            name="Winter Peak Schedule",
            starts_on=date(2026, 11, 1),
            ends_on=date(2026, 11, 30),
            hours=hours,
        ),
    )

    kwargs = svc.inventory_repo.insert.await_args.kwargs
    assert kwargs["data"]["hours"] == serialized_hours
    assert kwargs["data"]["created_by_user_id"] == USER_ID


@pytest.mark.asyncio
async def test_create_maintenance_and_slot_block_use_inventory_insert() -> None:
    """Maintenance and slot block routes share the inventory insert helper."""
    svc = _service()
    svc.inventory_repo.insert = AsyncMock(return_value={"id": "row-1", "facility_id": FACILITY_ID})

    await svc.create_maintenance(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        body=CreateMaintenanceWindowRequest(
            on_date=date(2026, 11, 15),
            from_min=600,
            to_min=720,
            note="Annual court resurfacing and lighting maintenance",
        ),
    )
    await svc.create_slot_block(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        body=CreateSlotBlockRequest(
            starts_on=date(2026, 11, 20),
            from_min=900,
            to_min=960,
            reason="Community Tournament Preparation",
        ),
    )

    assert svc.inventory_repo.insert.await_count == 2
    tables = [call.args[0] for call in svc.inventory_repo.insert.await_args_list]
    assert tables == ["facility_maintenance_windows", "facility_slot_blocks"]


@pytest.mark.asyncio
async def test_insert_inventory_maps_duplicate_to_conflict() -> None:
    """Unique and exclusion violations become 409 responses."""
    svc = _service()
    svc.inventory_repo.insert = AsyncMock(side_effect=UniqueViolationError("duplicate"))

    with pytest.raises(ConflictException):
        await svc.create_closure(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=CreateClosureRequest(closed_on=date(2026, 12, 25), reason="Holiday"),
        )

    svc.inventory_repo.insert = AsyncMock(side_effect=asyncpg.ExclusionViolationError("overlap"))
    with pytest.raises(ConflictException):
        await svc.create_schedule(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=CreateSchedulePeriodRequest(
                name="Winter",
                starts_on=date(2026, 11, 1),
                ends_on=date(2026, 11, 30),
                hours=_week_hours(),
            ),
        )


@pytest.mark.asyncio
async def test_insert_inventory_maps_integrity_errors_to_validation() -> None:
    """Foreign-key and check violations become 422 instead of opaque 500s."""
    svc = _service()

    svc.inventory_repo.insert = AsyncMock(
        side_effect=ForeignKeyViolationError('violates foreign key constraint "created_by_user_id"')
    )
    with pytest.raises(ValidationException):
        await svc.create_closure(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=CreateClosureRequest(closed_on=date(2026, 12, 25), reason="Holiday"),
        )

    svc.inventory_repo.insert = AsyncMock(
        side_effect=CheckViolationError(
            'violates check constraint "facility_slot_blocks_minutes_chk"'
        )
    )
    with pytest.raises(ValidationException):
        await svc.create_slot_block(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=CreateSlotBlockRequest(
                starts_on=date(2026, 11, 20),
                from_min=900,
                to_min=960,
                reason="Prep",
            ),
        )
