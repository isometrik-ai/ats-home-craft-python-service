"""Unit tests for FacilityBookingConfigService inventory operations."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from asyncpg import CheckViolationError, ForeignKeyViolationError, UniqueViolationError

from apps.user_service.app.schemas.enums import (
    FACILITY_BOOKING_MAX_UNITS,
    FacilityBookingArchetype,
)
from apps.user_service.app.schemas.facility_booking_config import (
    DayHours,
    UpdateFacilityBookingConfigRequest,
)
from apps.user_service.app.schemas.facility_booking_inventory import (
    CreateBookingUnitRequest,
    CreateClosureRequest,
    CreateMaintenanceWindowRequest,
    CreateSchedulePeriodRequest,
    CreateSlotBlockRequest,
    UpdateBookingUnitRequest,
    UpdateProjectBookingSettingsRequest,
    UpdateSchedulePeriodRequest,
    UpsertStaffAssignmentRequest,
)
from apps.user_service.app.services.facility_booking.defaults import (
    default_booking_config,
)
from apps.user_service.app.services.facility_booking_config_service import (
    FacilityBookingConfigService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)

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
    svc.config_repo.get_config = AsyncMock(return_value=_config_row())
    svc.staff_repo = MagicMock()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    return svc


def _config_row() -> dict:
    defaults = default_booking_config(FacilityBookingArchetype.SLOT)
    return {
        "id": "cfg-1",
        "facility_id": FACILITY_ID,
        "facility_name": "Club Pool",
        "facility_type": "sports",
        "archetype": FacilityBookingArchetype.SLOT.value,
        "slot_minutes": defaults.slot_minutes,
        "accepting_bookings": True,
        "default_hours": [h.model_dump(mode="json") for h in defaults.default_hours],
        "pricing": defaults.pricing.model_dump(mode="json"),
        "policies": defaults.policies.model_dump(mode="json"),
        "setup": defaults.setup.model_dump(mode="json"),
        "description": "Outdoor pool",
        "version": 2,
    }


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


@pytest.mark.asyncio
async def test_timezone_for_and_local_now() -> None:
    svc = _service()
    svc.config_repo.get_settings = AsyncMock(return_value={"timezone": "Asia/Kolkata"})
    assert await svc.timezone_for(PROJECT_ID) == "Asia/Kolkata"

    svc.config_repo.get_settings = AsyncMock(return_value=None)
    svc.config_repo.get_organization_timezone = AsyncMock(return_value="Europe/London")
    assert await svc.timezone_for(PROJECT_ID) == "Europe/London"

    svc.config_repo.get_organization_timezone = AsyncMock(return_value=None)
    assert await svc.timezone_for(PROJECT_ID) == "UTC"

    now = await svc.local_now(PROJECT_ID)
    assert now.tzinfo is None


@pytest.mark.asyncio
async def test_list_bookable_facilities_and_workspace() -> None:
    svc = _service()
    svc.config_repo.list_configs = AsyncMock(return_value=[_config_row()])
    svc.inventory_repo.list_rows = AsyncMock(
        return_value=[{"id": "u1", "name": "Lane 1", "active": True}]
    )
    cards = await svc.list_bookable_facilities(project_id=PROJECT_ID)
    assert cards[0]["name"] == "Club Pool"
    assert cards[0]["units"][0]["name"] == "Lane 1"

    svc.inventory_repo.load_all = AsyncMock(
        return_value={
            "facility_booking_units": [],
            "facility_schedule_periods": [],
            "facility_slot_blocks": [],
            "facility_closures": [],
            "facility_maintenance_windows": [],
        }
    )
    workspace = await svc.get_workspace(project_id=PROJECT_ID, facility_id=FACILITY_ID)
    assert workspace["config"]["facility_id"] == FACILITY_ID
    assert workspace["unit_count"] == 0


@pytest.mark.asyncio
async def test_update_config_conflict_and_success() -> None:
    svc = _service()
    svc.config_repo.update_config = AsyncMock(return_value=None)
    with pytest.raises(ConflictException):
        await svc.update_config(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=UpdateFacilityBookingConfigRequest(version=2, accepting_bookings=False),
        )

    updated_row = {**_config_row(), "description": "Updated"}
    svc.config_repo.update_config = AsyncMock(return_value=updated_row)
    svc.config_repo.get_config = AsyncMock(return_value=updated_row)
    svc.inventory_repo.load_all = AsyncMock(
        return_value={
            "facility_booking_units": [],
            "facility_schedule_periods": [],
            "facility_slot_blocks": [],
            "facility_closures": [],
            "facility_maintenance_windows": [],
        }
    )
    result = await svc.update_config(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        body=UpdateFacilityBookingConfigRequest(
            version=2, description="Updated", clear_policies_document=True
        ),
    )
    assert result["config"]["description"] == "Updated"


@pytest.mark.asyncio
async def test_create_unit_enforces_max_and_update_unit_paths() -> None:
    svc = _service()
    svc.inventory_repo.count_units = AsyncMock(return_value=FACILITY_BOOKING_MAX_UNITS)
    with pytest.raises(ValidationException):
        await svc.create_unit(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=CreateBookingUnitRequest(name="Lane 9"),
        )

    svc.inventory_repo.count_units = AsyncMock(return_value=0)
    svc.inventory_repo.insert = AsyncMock(return_value={"id": "u9", "name": "Lane 9"})
    created = await svc.create_unit(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        body=CreateBookingUnitRequest(name="Lane 9"),
    )
    assert created["id"] == "u9"

    svc.inventory_repo.get = AsyncMock(return_value={"id": "u1", "name": "Lane 1"})
    unchanged = await svc.update_unit(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        unit_id="u1",
        body=UpdateBookingUnitRequest(),
    )
    assert unchanged["name"] == "Lane 1"

    svc.inventory_repo.update = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.update_unit(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            unit_id="missing",
            body=UpdateBookingUnitRequest(name="Renamed"),
        )


@pytest.mark.asyncio
async def test_update_schedule_validates_range_and_overlap() -> None:
    svc = _service()
    svc.inventory_repo.get = AsyncMock(
        return_value={
            "id": "sched-1",
            "starts_on": date(2026, 11, 1),
            "ends_on": date(2026, 11, 30),
        }
    )
    with pytest.raises(ValidationException):
        await svc.update_schedule(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            period_id="sched-1",
            body=UpdateSchedulePeriodRequest(
                starts_on=date(2026, 12, 1), ends_on=date(2026, 11, 1)
            ),
        )

    svc.inventory_repo.update = AsyncMock(side_effect=asyncpg.ExclusionViolationError("overlap"))
    with pytest.raises(ConflictException):
        await svc.update_schedule(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            period_id="sched-1",
            body=UpdateSchedulePeriodRequest(name="Winter"),
        )


@pytest.mark.asyncio
async def test_create_slot_block_requires_unit_and_delete_inventory() -> None:
    svc = _service()
    svc.inventory_repo.get = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.create_slot_block(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=CreateSlotBlockRequest(
                starts_on=date(2026, 11, 20),
                from_min=900,
                to_min=960,
                reason="Prep",
                unit_id="missing-unit",
            ),
        )

    svc.inventory_repo.delete = AsyncMock(return_value=False)
    with pytest.raises(NotFoundException):
        await svc.delete_inventory(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            table="facility_closures",
            row_id="c1",
        )


@pytest.mark.asyncio
async def test_settings_and_staff_assignments() -> None:
    svc = _service()
    svc.config_repo.get_settings = AsyncMock(return_value=None)
    svc.config_repo.get_organization_timezone = AsyncMock(return_value="UTC")
    settings = await svc.get_settings(project_id=PROJECT_ID)
    assert settings["currency_code"] == "INR"
    assert settings["wallet_enabled"] is True

    svc.config_repo.upsert_settings = AsyncMock(
        return_value={
            "timezone": "Asia/Kolkata",
            "currency_code": "INR",
            "invoice_frequency": "monthly",
            "wallet_enabled": True,
            "online_enabled": True,
            "cash_enabled": False,
            "wallet_credit_limit": 5000,
            "updated_at": None,
        }
    )
    updated = await svc.update_settings(
        project_id=PROJECT_ID,
        body=UpdateProjectBookingSettingsRequest(timezone="Asia/Kolkata"),
    )
    assert updated["timezone"] == "Asia/Kolkata"

    svc.staff_repo.list_assignments = AsyncMock(return_value=[])
    assert await svc.list_staff_assignments(project_id=PROJECT_ID) == []

    svc.staff_repo.get_member = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.upsert_staff_assignment(
            project_id=PROJECT_ID,
            body=UpsertStaffAssignmentRequest(project_member_id="pm-1", facility_ids=[FACILITY_ID]),
        )

    svc.staff_repo.get_member = AsyncMock(return_value={"id": "pm-1"})
    svc.staff_repo.upsert_assignment = AsyncMock(return_value="assign-1")
    svc.staff_repo.list_assignments = AsyncMock(
        return_value=[{"id": "assign-1", "project_member_id": "pm-1"}]
    )
    row = await svc.upsert_staff_assignment(
        project_id=PROJECT_ID,
        body=UpsertStaffAssignmentRequest(project_member_id="pm-1", facility_ids=[FACILITY_ID]),
    )
    assert row["id"] == "assign-1"

    svc.staff_repo.delete_assignment = AsyncMock(return_value=False)
    with pytest.raises(NotFoundException):
        await svc.delete_staff_assignment(project_id=PROJECT_ID, assignment_id="missing")


@pytest.mark.asyncio
async def test_require_config_not_found_and_fk_without_actor() -> None:
    svc = _service()
    svc.config_repo.get_config = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.get_workspace(project_id=PROJECT_ID, facility_id=FACILITY_ID)

    svc.config_repo.get_config = AsyncMock(return_value=_config_row())
    svc.inventory_repo.insert = AsyncMock(
        side_effect=ForeignKeyViolationError('violates foreign key constraint "facility_id"')
    )
    with pytest.raises(ValidationException):
        await svc.create_closure(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=CreateClosureRequest(closed_on=date(2026, 12, 25), reason="Holiday"),
        )


@pytest.mark.asyncio
async def test_update_config_applies_pricing_and_setup() -> None:
    svc = _service()
    row = _config_row()
    svc.config_repo.get_config = AsyncMock(return_value=row)
    svc.config_repo.update_config = AsyncMock(return_value={**row, "slot_minutes": 30})
    svc.inventory_repo.load_all = AsyncMock(
        return_value={
            "facility_booking_units": [],
            "facility_schedule_periods": [],
            "facility_slot_blocks": [],
            "facility_closures": [],
            "facility_maintenance_windows": [],
        }
    )
    defaults = default_booking_config(FacilityBookingArchetype.SLOT)
    result = await svc.update_config(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        body=UpdateFacilityBookingConfigRequest(
            version=2,
            slot_minutes=45,
            pricing=defaults.pricing,
            policies=defaults.policies,
            setup=defaults.setup,
        ),
    )
    patch = svc.config_repo.update_config.await_args.kwargs["update_data"]
    assert patch["slot_minutes"] == defaults.setup.slot.durations[0]
    assert "pricing" in patch
    assert "setup" in patch
    assert result["config"]["facility_id"] == FACILITY_ID
