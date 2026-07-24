"""Unit tests for FacilitiesService with mocked repositories."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import (
    FacilityLocationType,
    FacilityStatus,
    ParkingUserType,
)
from apps.user_service.app.schemas.project_inventory import (
    CreateFacilityRequest,
    UpdateFacilityRequest,
)
from apps.user_service.app.services.facilities_service import FacilitiesService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
FACILITY_ID = "22222222-2222-2222-2222-222222222222"


def _ctx() -> UserContext:
    return UserContext(user_id="user-1", email="owner@example.com", organization_id="org-1")


def _service() -> FacilitiesService:
    svc = FacilitiesService(db_connection=MagicMock(), user_context=_ctx())
    svc.facilities_repo = MagicMock()
    svc.parking_slots_repo = MagicMock()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock(return_value={"id": PROJECT_ID})
    svc.setup_service.complete_step = AsyncMock(return_value={"step_key": "facilities"})
    svc.facilities_repo.insert_facility = AsyncMock(
        return_value={"id": FACILITY_ID, "name": "Pool", "facility_type": "amenity"}
    )
    svc.facilities_repo.list_facilities = AsyncMock(
        return_value=[{"id": FACILITY_ID, "name": "Pool"}]
    )
    svc.facilities_repo.get_facility = AsyncMock(
        return_value={"id": FACILITY_ID, "name": "Pool", "facility_type": "amenity"}
    )
    svc.facilities_repo.update_facility = AsyncMock(
        return_value={"id": FACILITY_ID, "name": "Pool Updated", "facility_type": "amenity"}
    )
    svc.facilities_repo.delete_facility = AsyncMock()
    svc.parking_slots_repo.bulk_insert_slots = AsyncMock()
    svc.parking_slots_repo.list_by_facility = AsyncMock(return_value=[{"id": "slot-1"}])
    svc.parking_slots_repo.delete_by_facility = AsyncMock()
    return svc


def _create_body(**overrides) -> CreateFacilityRequest:
    base = {
        "name": "Visitor Parking",
        "facility_type": "parking",
        "location_type": FacilityLocationType.OUTDOOR_STANDALONE,
        "parking_slots": 10,
        "parking_user_type": ParkingUserType.VISITORS,
    }
    base.update(overrides)
    return CreateFacilityRequest(**base)


@pytest.mark.asyncio
async def test_create_facility_provisions_parking_slots():
    """Parking facilities bulk-insert numbered slots after insert."""
    svc = _service()
    result = await svc.create_facility(project_id=PROJECT_ID, body=_create_body())

    assert result["id"] == FACILITY_ID
    svc.parking_slots_repo.bulk_insert_slots.assert_awaited_once()
    kwargs = svc.parking_slots_repo.bulk_insert_slots.await_args.kwargs
    assert kwargs["slot_count"] == 10


@pytest.mark.asyncio
async def test_create_facility_non_parking_skips_slots():
    """Non-parking facilities do not provision parking slots."""
    svc = _service()
    body = CreateFacilityRequest(
        name="Gym",
        facility_type="gym",
        location_type=FacilityLocationType.OUTDOOR_STANDALONE,
    )

    await svc.create_facility(project_id=PROJECT_ID, body=body)

    svc.parking_slots_repo.bulk_insert_slots.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_facilities_returns_serialized_rows():
    """list_facilities ensures project scope and serializes rows."""
    svc = _service()
    rows = await svc.list_facilities(project_id=PROJECT_ID)

    assert len(rows) == 1
    svc.setup_service.ensure_project.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_parking_slots_for_facility():
    """list_parking_slots delegates to parking repo after facility check."""
    svc = _service()
    rows = await svc.list_parking_slots(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        status="available",
    )

    assert rows[0]["id"] == "slot-1"
    svc.parking_slots_repo.list_by_facility.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_facility_not_found():
    """Missing facility raises NotFoundException."""
    svc = _service()
    svc.facilities_repo.get_facility = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.update_facility(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=UpdateFacilityRequest(name="X"),
        )


@pytest.mark.asyncio
async def test_update_facility_merges_and_validates():
    """update_facility merges patch with current row before validation."""
    svc = _service()
    updated = await svc.update_facility(
        project_id=PROJECT_ID,
        facility_id=FACILITY_ID,
        body=UpdateFacilityRequest(name="Pool Updated", status=FacilityStatus.INACTIVE),
    )

    assert updated["name"] == "Pool Updated"
    svc.facilities_repo.update_facility.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_facility_rejects_invalid_in_tower_without_wing():
    """Merged payload must satisfy conditional facility validation."""
    svc = _service()
    svc.facilities_repo.get_facility = AsyncMock(
        return_value={
            "id": FACILITY_ID,
            "name": "Club",
            "facility_type": "club",
            "location_type": FacilityLocationType.IN_TOWER.value,
        }
    )

    with pytest.raises(ValidationException):
        await svc.update_facility(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=UpdateFacilityRequest(name="Clubhouse"),
        )


@pytest.mark.asyncio
async def test_delete_facility_removes_slots_then_row():
    """delete_facility clears parking slots before deleting facility."""
    svc = _service()
    result = await svc.delete_facility(project_id=PROJECT_ID, facility_id=FACILITY_ID)

    svc.parking_slots_repo.delete_by_facility.assert_awaited_once()
    svc.facilities_repo.delete_facility.assert_awaited_once()
    assert result["old_data"]["id"] == FACILITY_ID
    assert result["new_data"] is None


@pytest.mark.asyncio
async def test_complete_facilities_marks_step():
    """complete_facilities delegates to setup service."""
    svc = _service()
    result = await svc.complete_facilities(project_id=PROJECT_ID)

    assert result["step_key"] == "facilities"
    svc.setup_service.complete_step.assert_awaited_once()
