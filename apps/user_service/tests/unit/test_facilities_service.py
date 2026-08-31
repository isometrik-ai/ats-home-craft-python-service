"""Unit tests for FacilitiesService with mocked repositories."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import (
    FacilityLocationType,
    FacilityStatus,
    FacilityType,
    ParkingUserType,
    ParkingVehicleCategory,
    UnitNumberingPattern,
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
TOWER_ID = "33333333-3333-3333-3333-333333333333"


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
        return_value={"id": FACILITY_ID, "name": "Pool", "facility_type": "recreation"}
    )
    svc.facilities_repo.list_facilities = AsyncMock(
        return_value=(
            [
                {
                    "id": FACILITY_ID,
                    "name": "Visitor Parking",
                    "facility_type": "parking",
                    "parking_vehicle_category": "four_wheeler",
                }
            ],
            1,
        )
    )
    svc.facilities_repo.get_facility = AsyncMock(
        return_value={"id": FACILITY_ID, "name": "Pool", "facility_type": "recreation"}
    )
    svc.facilities_repo.update_facility = AsyncMock(
        return_value={"id": FACILITY_ID, "name": "Pool Updated", "facility_type": "recreation"}
    )
    svc.facilities_repo.delete_facility = AsyncMock()
    svc.parking_slots_repo.bulk_insert_slots = AsyncMock()
    svc.parking_slots_repo.list_by_facility = AsyncMock(return_value=[{"id": "slot-1"}])
    svc.parking_slots_repo.delete_by_facility = AsyncMock()
    svc.towers_repo = MagicMock()
    svc.towers_repo.get_tower = AsyncMock(return_value={"id": TOWER_ID, "has_wings": True})
    return svc


def _create_body(**overrides) -> CreateFacilityRequest:
    base = {
        "name": "Visitor Parking",
        "facility_type": FacilityType.PARKING,
        "location_type": FacilityLocationType.OUTDOOR_STANDALONE,
        "parking_slots": 10,
        "parking_user_type": ParkingUserType.VISITORS,
        "parking_vehicle_category": ParkingVehicleCategory.FOUR_WHEELER,
        "facility_subtype": "open",
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
    assert kwargs["slots"] == [(i, str(i)) for i in range(1, 11)]


@pytest.mark.asyncio
async def test_create_parking_facility_builds_custom_slot_codes():
    """Custom numbering generates prefixed slot_code values."""
    svc = _service()
    await svc.create_facility(
        project_id=PROJECT_ID,
        body=_create_body(
            parking_slots=3,
            numbering_pattern=UnitNumberingPattern.CUSTOM,
            custom_prefix="SLT-A",
        ),
    )

    kwargs = svc.parking_slots_repo.bulk_insert_slots.await_args.kwargs
    assert kwargs["slots"] == [(1, "SLT-A-1"), (2, "SLT-A-2"), (3, "SLT-A-3")]


@pytest.mark.asyncio
async def test_create_parking_facility_honors_starting_slots_number():
    """Parking facilities can start slot numbering from a custom offset."""
    svc = _service()
    await svc.create_facility(
        project_id=PROJECT_ID,
        body=_create_body(starting_slots_number=100, parking_slots=2),
    )

    kwargs = svc.parking_slots_repo.bulk_insert_slots.await_args.kwargs
    assert kwargs["slots"] == [(100, "100"), (101, "101")]


@pytest.mark.asyncio
async def test_create_parking_facility_rejects_custom_pattern_without_prefix():
    """Custom parking numbering requires custom_prefix."""
    svc = _service()
    with pytest.raises(ValidationException):
        await svc.create_facility(
            project_id=PROJECT_ID,
            body=_create_body(numbering_pattern=UnitNumberingPattern.CUSTOM),
        )


@pytest.mark.asyncio
async def test_create_facility_non_parking_rejects_numbering_fields():
    """Numbering fields are only valid for parking facilities."""
    svc = _service()
    with pytest.raises(ValidationException):
        await svc.create_facility(
            project_id=PROJECT_ID,
            body=CreateFacilityRequest(
                name="Gym",
                facility_type=FacilityType.SPORTS,
                location_type=FacilityLocationType.OUTDOOR_STANDALONE,
                numbering_pattern=UnitNumberingPattern.SEQUENTIAL,
            ),
        )


@pytest.mark.asyncio
async def test_create_facility_non_parking_skips_slots():
    """Non-parking facilities do not provision parking slots."""
    svc = _service()
    body = CreateFacilityRequest(
        name="Gym",
        facility_type=FacilityType.SPORTS,
        location_type=FacilityLocationType.OUTDOOR_STANDALONE,
    )

    await svc.create_facility(project_id=PROJECT_ID, body=body)

    svc.parking_slots_repo.bulk_insert_slots.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_facilities_forwards_search_to_repository():
    """list_facilities passes search through to the repository."""
    svc = _service()
    await svc.list_facilities(project_id=PROJECT_ID, search="Gym")

    svc.facilities_repo.list_facilities.assert_awaited_once_with(
        organization_id="org-1",
        project_id=PROJECT_ID,
        facility_types=None,
        status=None,
        search="Gym",
        page=1,
        page_size=20,
    )


@pytest.mark.asyncio
async def test_list_facilities_returns_serialized_rows():
    """list_facilities ensures project scope and serializes rows."""
    svc = _service()
    result = await svc.list_facilities(project_id=PROJECT_ID)

    assert len(result["items"]) == 1
    assert result["total"] == 1
    assert result["items"][0]["parking_vehicle_category"] == "four_wheeler"
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
            "facility_type": "recreation",
            "location_type": FacilityLocationType.IN_TOWER.value,
            "tower_id": TOWER_ID,
        }
    )

    with pytest.raises(ValidationException):
        await svc.update_facility(
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=UpdateFacilityRequest(name="Clubhouse"),
        )


@pytest.mark.asyncio
async def test_create_facility_in_tower_without_wing_when_tower_has_no_wings():
    """Wingless towers allow in_tower facilities with tower_id and floor only."""
    svc = _service()
    svc.towers_repo.get_tower = AsyncMock(return_value={"id": TOWER_ID, "has_wings": False})
    body = CreateFacilityRequest(
        name="Kids Play Area",
        facility_type=FacilityType.RECREATION,
        location_type=FacilityLocationType.IN_TOWER,
        tower_id=TOWER_ID,
        floor_level="G+1",
    )

    result = await svc.create_facility(project_id=PROJECT_ID, body=body)

    assert result["id"] == FACILITY_ID
    svc.towers_repo.get_tower.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_facility_in_tower_requires_wing_when_tower_has_wings():
    """Towers with wings still require wing on in_tower facilities."""
    svc = _service()
    body = CreateFacilityRequest(
        name="Gym",
        facility_type=FacilityType.RECREATION,
        location_type=FacilityLocationType.IN_TOWER,
        tower_id=TOWER_ID,
        floor_level="G+1",
    )

    with pytest.raises(ValidationException):
        await svc.create_facility(project_id=PROJECT_ID, body=body)


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
