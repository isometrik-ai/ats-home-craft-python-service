"""Unit tests for ParkingAllotmentService."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import (
    ParkingAllotmentBasis,
    ParkingFacilitySubtype,
    ParkingSlotDisplayStatus,
    ParkingVehicleCategory,
)
from apps.user_service.app.schemas.parking_allotment import AllotParkingSlotRequest
from apps.user_service.app.services.parking_allotment_service import (
    ParkingAllotmentService,
)
from apps.user_service.app.utils.common_utils import UserContext


def _user_context() -> UserContext:
    return UserContext(
        user_id="staff-1",
        email="staff@example.com",
        organization_id="org-1",
    )


def _slot_row(**overrides: object) -> dict[str, object]:
    row = {
        "id": "slot-1",
        "slot_number": 2,
        "slot_status": "available",
        "facility_id": "facility-1",
        "facility_name": "Bay B",
        "floor_level": "B2",
        "wing": "Bay B",
        "tower_id": "tower-1",
        "tower_code": "A",
        "tower_name": "Tower A",
        "display_status": ParkingSlotDisplayStatus.FREE.value,
        "slot_type": ParkingFacilitySubtype.OPEN.value,
        "parking_vehicle_category": "four_wheeler",
        "unit_id": None,
        "unit_code": None,
        "allotment_basis": None,
        "effective_from": None,
        "allotted_at": None,
        "updated_at": datetime.now(timezone.utc),
    }
    row.update(overrides)
    return row


def test_build_slot_code_label_uses_slot_code_when_present():
    assert (
        ParkingAllotmentService._build_slot_code_label(
            {
                "tower_code": "A",
                "floor_level": "B2",
                "slot_code": "SLT-A-9",
                "slot_number": 9,
            }
        )
        == "A-B2-SLT-A-9"
    )


def test_build_slot_code_label_omits_missing_tower_and_floor():
    assert (
        ParkingAllotmentService._build_slot_code_label(
            {
                "tower_code": "A",
                "floor_level": "B2",
                "slot_number": 9,
            }
        )
        == "A-B2-009"
    )
    assert (
        ParkingAllotmentService._build_slot_code_label(
            {
                "floor_level": "B2",
                "slot_number": 9,
            }
        )
        == "B2-009"
    )
    assert (
        ParkingAllotmentService._build_slot_code_label(
            {
                "tower_code": "A",
                "slot_number": 9,
            }
        )
        == "A-009"
    )


def test_build_slot_code_formats_tower_floor_and_number():
    assert (
        ParkingAllotmentService._build_slot_code(
            tower_code="A",
            floor_level="B2",
            slot_number=2,
        )
        == "A-B2-002"
    )


def test_resolve_slot_code_prefers_persisted_value():
    assert (
        ParkingAllotmentService._resolve_slot_code(
            {
                "slot_code": "SLT-A-2",
                "tower_code": "A",
                "floor_level": "B2",
                "slot_number": 2,
            }
        )
        == "SLT-A-2"
    )


def test_slot_allowed_actions_for_free_slot():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    assert "allot" in svc._slot_allowed_actions(display_status=ParkingSlotDisplayStatus.FREE.value)


def test_unit_allowed_actions_when_short_on_entitlement():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    assert svc._unit_allowed_actions(
        two_wheeler_parking_entitlement=1,
        four_wheeler_parking_entitlement=1,
        included_two_wheeler_slots_assigned=0,
        included_four_wheeler_slots_assigned=0,
        slots_assigned=0,
    ) == ["allot_slot"]


def test_unit_allowed_actions_when_entitlement_met():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    assert svc._unit_allowed_actions(
        two_wheeler_parking_entitlement=1,
        four_wheeler_parking_entitlement=1,
        included_two_wheeler_slots_assigned=1,
        included_four_wheeler_slots_assigned=1,
        slots_assigned=2,
    ) == ["add_slot"]


@pytest.mark.asyncio
async def test_resolve_list_scope_validates_parking_facility():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.facilities_repo = MagicMock()
    svc.facilities_repo.get_facility = AsyncMock(
        return_value={
            "id": "facility-1",
            "facility_type": "parking",
            "tower_id": "tower-1",
        }
    )

    tower_id, facility_id = await svc._resolve_list_scope(
        project_id="project-1",
        tower_id=None,
        facility_id="facility-1",
    )

    assert tower_id == "tower-1"
    assert facility_id == "facility-1"


@pytest.mark.asyncio
async def test_get_unit_returns_slots_held():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_unit_for_allotment_view = AsyncMock(
        return_value={
            "id": "unit-1",
            "code": "A-1804",
            "configuration_label": "3 BHK",
            "two_wheeler_parking_entitlement": 1,
            "four_wheeler_parking_entitlement": 1,
            "slots_assigned": 1,
            "active_allotments": [
                {
                    "allotment_id": "allotment-1",
                    "slot_id": "slot-1",
                    "effective_from": date(2026, 8, 16),
                    "allotment_basis": ParkingAllotmentBasis.INCLUDED_WITH_UNIT.value,
                }
            ],
        }
    )
    svc.repo.get_slot_row = AsyncMock(return_value=_slot_row(slot_code="SLT-A-1"))

    result = await svc.get_unit(project_id="project-1", unit_id="unit-1")

    assert result.code == "A-1804"
    assert result.slots_assigned == 1
    assert len(result.slots_held) == 1
    assert result.slots_held[0].slot_code == "SLT-A-1"
    assert result.slots_held[0].slot_code_label == "A-B2-SLT-A-1"


@pytest.mark.asyncio
async def test_get_unit_parses_json_string_active_allotments():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_unit_for_allotment_view = AsyncMock(
        return_value={
            "id": "unit-1",
            "code": "A-1804",
            "configuration_label": "3 BHK",
            "two_wheeler_parking_entitlement": 0,
            "four_wheeler_parking_entitlement": 1,
            "slots_assigned": 1,
            "included_two_wheeler_slots_assigned": 0,
            "included_four_wheeler_slots_assigned": 1,
            "active_allotments": (
                '[{"allotment_id": "allotment-1", "slot_id": "slot-1", '
                '"effective_from": "2026-08-16", '
                '"allotment_basis": "included_with_unit"}]'
            ),
        }
    )
    svc.repo.get_slot_row = AsyncMock(return_value=_slot_row(slot_code="SLT-A-1"))

    result = await svc.get_unit(project_id="project-1", unit_id="unit-1")

    assert result.slots_assigned == 1
    assert len(result.slots_held) == 1
    assert result.slots_held[0].slot_id == "slot-1"
    assert result.slots_held[0].slot_code == "SLT-A-1"
    assert result.slots_held[0].slot_code_label == "A-B2-SLT-A-1"


@pytest.mark.asyncio
async def test_list_slot_history_parses_json_string_payload():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_slot_row = AsyncMock(return_value={"id": "slot-1"})
    svc.repo.list_slot_history = AsyncMock(
        return_value=[
            {
                "id": "event-1",
                "event_type": "allotted",
                "unit_id": "unit-1",
                "unit_code": "A-1804",
                "allotment_id": "allotment-1",
                "actor_user_id": "staff-1",
                "payload": '{"allotment_basis": "included_with_unit"}',
                "occurred_at": datetime(2026, 8, 16, tzinfo=timezone.utc),
            }
        ]
    )

    items = await svc.list_slot_history(project_id="project-1", slot_id="slot-1")

    assert len(items) == 1
    assert items[0].payload == {"allotment_basis": "included_with_unit"}


@pytest.mark.asyncio
async def test_get_unit_not_found():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_unit_for_allotment_view = AsyncMock(return_value=None)

    from libs.shared_utils.http_exceptions import NotFoundException

    with pytest.raises(NotFoundException):
        await svc.get_unit(project_id="project-1", unit_id="missing-unit")


@pytest.mark.asyncio
async def test_validate_unit_rejects_four_wheeler_entitlement_full():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.repo = MagicMock()
    svc.repo.get_unit_allotment_context = AsyncMock(
        return_value={
            "id": "unit-1",
            "code": "A-1804",
            "is_parking": False,
            "two_wheeler_parking_entitlement": 0,
            "four_wheeler_parking_entitlement": 1,
            "included_slots_assigned": 1,
            "included_two_wheeler_slots_assigned": 0,
            "included_four_wheeler_slots_assigned": 1,
            "slots_assigned": 1,
        }
    )

    from libs.shared_utils.http_exceptions import ValidationException

    with pytest.raises(ValidationException):
        await svc._validate_unit_for_allotment(
            project_id="project-1",
            unit_id="unit-1",
            allotment_basis=ParkingAllotmentBasis.INCLUDED_WITH_UNIT,
            slot_row=_slot_row(
                slot_type=ParkingFacilitySubtype.BASEMENT.value,
                parking_vehicle_category="four_wheeler",
            ),
        )


@pytest.mark.asyncio
async def test_allot_slot_creates_allotment_and_assigns_slot():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.slots_repo = MagicMock()
    svc.repo.get_unit_allotment_context = AsyncMock(
        return_value={
            "id": "unit-1",
            "code": "A-1804",
            "is_parking": False,
            "two_wheeler_parking_entitlement": 0,
            "four_wheeler_parking_entitlement": 2,
            "included_slots_assigned": 0,
            "included_two_wheeler_slots_assigned": 0,
            "included_four_wheeler_slots_assigned": 0,
            "slots_assigned": 0,
        }
    )
    svc.repo.insert_allotment = AsyncMock(return_value={"id": "allotment-1"})
    svc.slots_repo.assign_slot = AsyncMock(return_value={"id": "slot-1", "status": "assigned"})
    svc.repo.insert_event = AsyncMock()
    svc.repo.get_slot_row = AsyncMock(
        side_effect=[
            _slot_row(),
            _slot_row(
                display_status=ParkingSlotDisplayStatus.ALLOTTED.value,
                unit_id="unit-1",
                unit_code="A-1804",
                allotment_basis=ParkingAllotmentBasis.INCLUDED_WITH_UNIT.value,
                effective_from=date(2026, 8, 16),
            ),
        ]
    )

    result = await svc.allot_slot(
        project_id="project-1",
        slot_id="slot-1",
        body=AllotParkingSlotRequest(
            unit_id="unit-1",
            effective_from=date(2026, 8, 16),
            allotment_basis=ParkingAllotmentBasis.INCLUDED_WITH_UNIT,
        ),
    )

    assert result.slot_code == "A-B2-002"
    assert result.slot_code_label == "A-B2-002"
    assert result.allotted_to_unit is not None
    assert result.allotted_to_unit.code == "A-1804"
    assert result.parking_vehicle_category == ParkingVehicleCategory.FOUR_WHEELER
    svc.repo.insert_allotment.assert_awaited_once()
    svc.slots_repo.assign_slot.assert_awaited_once()
