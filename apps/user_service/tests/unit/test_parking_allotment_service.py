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
    VehicleType,
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


def test_build_slot_code_label_matches_custom_parking_search_example():
    """Exact slot search must target the same label shown in the by-slot table."""
    assert (
        ParkingAllotmentService._build_slot_code_label(
            {
                "tower_code": "LUX",
                "floor_level": "A",
                "slot_code": "B1-2",
                "slot_number": 2,
            }
        )
        == "LUX-A-B1-2"
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
async def test_validate_unit_allows_both_slot_for_two_wheeler_when_four_full():
    """Both-category slots use the vehicle type bucket during vehicle review."""
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.repo = MagicMock()
    svc.repo.get_unit_allotment_context = AsyncMock(
        return_value={
            "id": "unit-1",
            "code": "A-1804",
            "is_parking": False,
            "two_wheeler_parking_entitlement": 1,
            "four_wheeler_parking_entitlement": 1,
            "included_slots_assigned": 1,
            "included_two_wheeler_slots_assigned": 0,
            "included_four_wheeler_slots_assigned": 1,
            "slots_assigned": 1,
        }
    )

    unit = await svc._validate_unit_for_allotment(
        project_id="project-1",
        unit_id="unit-1",
        allotment_basis=ParkingAllotmentBasis.INCLUDED_WITH_UNIT,
        slot_row=_slot_row(parking_vehicle_category="both"),
        vehicle_type=VehicleType.TWO_WHEELER.value,
    )

    assert unit["id"] == "unit-1"


@pytest.mark.asyncio
async def test_validate_unit_rejects_both_slot_when_only_four_bucket_full_for_four_wheeler():
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
            slot_row=_slot_row(parking_vehicle_category="both"),
            vehicle_type=VehicleType.FOUR_WHEELER.value,
        )


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


def test_format_date_and_event_payload_helpers():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    assert svc._format_date(None) is None
    assert svc._format_date(date(2026, 3, 1)) == "2026-03-01"
    assert svc._format_date("2026-03-01") == "2026-03-01"
    assert svc._normalize_event_payload('{"a": 1}') == {"a": 1}
    assert svc._normalize_event_payload(["bad"]) == {}
    assert svc._active_allotments_from_row([{"allotment_id": "a1"}, "skip"]) == [
        {"allotment_id": "a1"}
    ]
    assert svc._slot_type_label("basement") == "Basement"
    assert svc._slot_type_label("custom_type") == "Custom Type"


def test_slot_allowed_actions_and_vehicle_category():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    assert svc._slot_allowed_actions(
        display_status=ParkingSlotDisplayStatus.VISITOR_POOL.value
    ) == [
        "details",
        "history",
    ]
    assert "reassign" in svc._slot_allowed_actions(
        display_status=ParkingSlotDisplayStatus.ALLOTTED.value
    )
    assert svc._slot_allowed_actions(display_status="unknown") == ["details", "history"]
    assert (
        ParkingAllotmentService._slot_vehicle_category({"parking_vehicle_category": "two_wheeler"})
        == "two_wheeler"
    )
    assert (
        ParkingAllotmentService._slot_vehicle_category({"parking_vehicle_category": "both"})
        == "both"
    )
    assert (
        ParkingAllotmentService._resolve_entitlement_bucket(
            slot_row={"parking_vehicle_category": "both"},
            vehicle_type=VehicleType.TWO_WHEELER.value,
        )
        == VehicleType.TWO_WHEELER.value
    )
    assert (
        ParkingAllotmentService._resolve_parking_vehicle_category(
            {"parking_vehicle_category": "two_wheeler"}
        )
        == ParkingVehicleCategory.TWO_WHEELER
    )
    assert ParkingAllotmentService._resolve_slot_type({}) == ParkingFacilitySubtype.OPEN.value


def test_unit_allowed_actions_without_entitlement():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    assert (
        svc._unit_allowed_actions(
            two_wheeler_parking_entitlement=0,
            four_wheeler_parking_entitlement=0,
            included_two_wheeler_slots_assigned=0,
            included_four_wheeler_slots_assigned=0,
            slots_assigned=0,
        )
        == []
    )


@pytest.mark.asyncio
async def test_resolve_list_scope_rejects_invalid_facility():
    from libs.shared_utils.http_exceptions import NotFoundException, ValidationException

    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.facilities_repo = MagicMock()
    svc.facilities_repo.get_facility = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc._resolve_list_scope(
            project_id="project-1",
            tower_id=None,
            facility_id="facility-1",
        )

    svc.facilities_repo.get_facility = AsyncMock(
        return_value={"id": "facility-1", "facility_type": "clubhouse", "tower_id": "tower-1"}
    )
    with pytest.raises(NotFoundException):
        await svc._resolve_list_scope(
            project_id="project-1",
            tower_id=None,
            facility_id="facility-1",
        )

    svc.facilities_repo.get_facility = AsyncMock(
        return_value={"id": "facility-1", "facility_type": "parking", "tower_id": "tower-1"}
    )
    with pytest.raises(ValidationException):
        await svc._resolve_list_scope(
            project_id="project-1",
            tower_id="tower-2",
            facility_id="facility-1",
        )


@pytest.mark.asyncio
async def test_get_summary_and_list_slots():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc._resolve_list_scope = AsyncMock(return_value=(None, None))
    svc.repo = MagicMock()
    svc.repo.get_summary = AsyncMock(
        return_value={
            "total_slots": 3,
            "allotted": 1,
            "free_to_allot": 2,
            "visitor_pool": 0,
            "blocked": 0,
            "units_short_of_entitlement": 0,
        }
    )
    svc.repo.list_slots = AsyncMock(return_value=([_slot_row()], 1))

    summary = await svc.get_summary(project_id="project-1")
    assert summary.total_slots == 3

    items, total = await svc.list_slots(
        project_id="project-1",
        page=1,
        page_size=20,
    )
    assert total == 1
    assert items[0].slot_code == "A-B2-002"


@pytest.mark.asyncio
async def test_get_slot_detail_and_history_not_found():
    from libs.shared_utils.http_exceptions import NotFoundException

    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.get_slot_row = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.get_slot_detail(project_id="project-1", slot_id="missing")

    with pytest.raises(NotFoundException):
        await svc.list_slot_history(project_id="project-1", slot_id="missing")


@pytest.mark.asyncio
async def test_list_units_loads_slot_metadata():
    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.repo.list_units = AsyncMock(
        return_value=(
            [
                {
                    "id": "unit-1",
                    "code": "A-1804",
                    "configuration_label": "3 BHK",
                    "two_wheeler_parking_entitlement": 0,
                    "four_wheeler_parking_entitlement": 0,
                    "slots_assigned": 0,
                    "included_two_wheeler_slots_assigned": 0,
                    "included_four_wheeler_slots_assigned": 0,
                    "active_allotments": [],
                }
            ],
            1,
        )
    )

    items, total = await svc.list_units(project_id="project-1", page=1, page_size=20)

    assert total == 1
    assert items[0].entitlement_status == "none"


@pytest.mark.asyncio
async def test_validate_slot_for_allotment_errors():
    from apps.user_service.app.schemas.enums import ParkingUserType
    from libs.shared_utils.http_exceptions import (
        ConflictException,
        NotFoundException,
        ValidationException,
    )

    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.repo = MagicMock()
    svc.repo.get_slot_row = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc._validate_slot_for_allotment(project_id="project-1", slot_id="slot-1")

    svc.repo.get_slot_row = AsyncMock(
        return_value=_slot_row(display_status=ParkingSlotDisplayStatus.ALLOTTED.value)
    )
    with pytest.raises(ConflictException):
        await svc._validate_slot_for_allotment(project_id="project-1", slot_id="slot-1")

    svc.repo.get_slot_row = AsyncMock(
        return_value=_slot_row(
            display_status=ParkingSlotDisplayStatus.FREE.value,
            parking_user_type=ParkingUserType.VISITORS.value,
        )
    )
    with pytest.raises(ValidationException):
        await svc._validate_slot_for_allotment(project_id="project-1", slot_id="slot-1")


@pytest.mark.asyncio
async def test_validate_unit_included_entitlement_branches():
    from libs.shared_utils.http_exceptions import NotFoundException, ValidationException

    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.repo = MagicMock()
    svc.repo.get_unit_allotment_context = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc._validate_unit_for_allotment(
            project_id="project-1",
            unit_id="unit-1",
            allotment_basis=ParkingAllotmentBasis.INCLUDED_WITH_UNIT,
        )

    svc.repo.get_unit_allotment_context = AsyncMock(
        return_value={
            "id": "unit-1",
            "is_parking": True,
            "two_wheeler_parking_entitlement": 0,
            "four_wheeler_parking_entitlement": 0,
        }
    )
    with pytest.raises(ValidationException):
        await svc._validate_unit_for_allotment(
            project_id="project-1",
            unit_id="unit-1",
            allotment_basis=ParkingAllotmentBasis.INCLUDED_WITH_UNIT,
        )

    svc.repo.get_unit_allotment_context = AsyncMock(
        return_value={
            "id": "unit-1",
            "is_parking": False,
            "two_wheeler_parking_entitlement": 0,
            "four_wheeler_parking_entitlement": 0,
            "included_slots_assigned": 0,
        }
    )
    with pytest.raises(ValidationException):
        await svc._validate_unit_for_allotment(
            project_id="project-1",
            unit_id="unit-1",
            allotment_basis=ParkingAllotmentBasis.INCLUDED_WITH_UNIT,
        )

    svc.repo.get_unit_allotment_context = AsyncMock(
        return_value={
            "id": "unit-1",
            "is_parking": False,
            "two_wheeler_parking_entitlement": 1,
            "four_wheeler_parking_entitlement": 0,
            "included_slots_assigned": 1,
            "included_two_wheeler_slots_assigned": 1,
            "included_four_wheeler_slots_assigned": 0,
        }
    )
    with pytest.raises(ValidationException):
        await svc._validate_unit_for_allotment(
            project_id="project-1",
            unit_id="unit-1",
            allotment_basis=ParkingAllotmentBasis.INCLUDED_WITH_UNIT,
        )


@pytest.mark.asyncio
async def test_allot_slot_assign_conflict_and_vehicle_review():
    from libs.shared_utils.http_exceptions import ConflictException

    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.slots_repo = MagicMock()
    svc.repo.get_unit_allotment_context = AsyncMock(
        return_value={
            "id": "unit-1",
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
    svc.slots_repo.assign_slot = AsyncMock(return_value=None)
    svc.repo.get_slot_row = AsyncMock(return_value=_slot_row())

    with pytest.raises(ConflictException):
        await svc.allot_slot(
            project_id="project-1",
            slot_id="slot-1",
            body=AllotParkingSlotRequest(
                unit_id="unit-1",
                effective_from=date(2026, 8, 16),
                allotment_basis=ParkingAllotmentBasis.INCLUDED_WITH_UNIT,
            ),
        )

    svc.slots_repo.assign_slot = AsyncMock(return_value={"id": "slot-1"})
    svc.repo.insert_event = AsyncMock()
    svc.repo.get_slot_row = AsyncMock(
        side_effect=[
            _slot_row(),
            _slot_row(display_status=ParkingSlotDisplayStatus.ALLOTTED.value, unit_id="unit-1"),
        ]
    )
    svc._create_allotment = AsyncMock()
    await svc.allot_slot_for_vehicle_review(
        project_id="project-1",
        unit_id="unit-1",
        slot_id="slot-1",
        vehicle_type=VehicleType.FOUR_WHEELER.value,
    )
    svc._create_allotment.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_reassign_block_and_unblock_slot():
    from apps.user_service.app.schemas.enums import ParkingSlotEventType
    from apps.user_service.app.schemas.parking_allotment import (
        BlockParkingSlotRequest,
        ReassignParkingSlotRequest,
        ReleaseParkingSlotRequest,
    )
    from libs.shared_utils.http_exceptions import ConflictException, NotFoundException

    svc = ParkingAllotmentService(db_connection=MagicMock(), user_context=_user_context())
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo = MagicMock()
    svc.slots_repo = MagicMock()
    svc.repo.get_active_allotment_by_slot = AsyncMock(
        return_value={"id": "allotment-1", "unit_id": "unit-1"}
    )
    svc.repo.release_allotment = AsyncMock(return_value={"id": "allotment-1"})
    svc.repo.clear_vehicle_slot_references = AsyncMock()
    svc.slots_repo.release_slot = AsyncMock()
    svc.repo.insert_event = AsyncMock()
    svc.repo.get_active_allotment_by_slot = AsyncMock(return_value=None)
    with pytest.raises(ConflictException):
        await svc.release_slot(
            project_id="project-1",
            slot_id="slot-1",
            body=ReleaseParkingSlotRequest(reason="done"),
        )

    svc.repo.get_active_allotment_by_slot = AsyncMock(
        return_value={"id": "allotment-1", "unit_id": "unit-1"}
    )
    svc.repo.get_slot_row = AsyncMock(
        side_effect=[
            _slot_row(display_status=ParkingSlotDisplayStatus.FREE.value),
            _slot_row(display_status=ParkingSlotDisplayStatus.FREE.value),
        ]
    )
    await svc.release_slot(
        project_id="project-1",
        slot_id="slot-1",
        body=ReleaseParkingSlotRequest(reason="done"),
    )
    svc.repo.insert_event.assert_awaited()
    assert (
        svc.repo.insert_event.await_args.kwargs["event_type"] == ParkingSlotEventType.RELEASED.value
    )

    svc.repo.get_slot_row = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.block_slot(
            project_id="project-1",
            slot_id="slot-1",
            body=BlockParkingSlotRequest(reason="maintenance"),
        )

    svc.repo.get_slot_row = AsyncMock(
        side_effect=[
            _slot_row(display_status=ParkingSlotDisplayStatus.FREE.value),
            _slot_row(display_status=ParkingSlotDisplayStatus.BLOCKED.value),
        ]
    )
    svc.slots_repo.block_slot = AsyncMock(return_value={"id": "slot-1"})
    await svc.block_slot(
        project_id="project-1",
        slot_id="slot-1",
        body=BlockParkingSlotRequest(reason="maintenance"),
    )

    svc.repo.get_slot_row = AsyncMock(
        side_effect=[
            _slot_row(display_status=ParkingSlotDisplayStatus.BLOCKED.value),
            _slot_row(display_status=ParkingSlotDisplayStatus.FREE.value),
        ]
    )
    svc.slots_repo.unblock_slot = AsyncMock(return_value={"id": "slot-1"})
    await svc.unblock_slot(project_id="project-1", slot_id="slot-1")

    svc.repo.get_slot_row = AsyncMock(
        return_value=_slot_row(display_status=ParkingSlotDisplayStatus.ALLOTTED.value)
    )
    svc.repo.get_active_allotment_by_slot = AsyncMock(
        return_value={"id": "allotment-1", "unit_id": "unit-old"}
    )
    svc.repo.release_allotment = AsyncMock(return_value={"id": "allotment-1"})
    svc.repo.insert_allotment = AsyncMock(return_value={"id": "allotment-2"})
    svc.slots_repo.assign_slot = AsyncMock(return_value={"id": "slot-1"})
    svc.repo.get_unit_allotment_context = AsyncMock(
        return_value={
            "id": "unit-new",
            "is_parking": False,
            "two_wheeler_parking_entitlement": 0,
            "four_wheeler_parking_entitlement": 2,
            "included_two_wheeler_slots_assigned": 0,
            "included_four_wheeler_slots_assigned": 0,
            "slots_assigned": 0,
        }
    )
    svc.repo.get_slot_row = AsyncMock(
        side_effect=[
            _slot_row(display_status=ParkingSlotDisplayStatus.ALLOTTED.value, unit_id="unit-old"),
            _slot_row(display_status=ParkingSlotDisplayStatus.ALLOTTED.value, unit_id="unit-new"),
        ]
    )
    await svc.reassign_slot(
        project_id="project-1",
        slot_id="slot-1",
        body=ReassignParkingSlotRequest(
            unit_id="unit-new",
            effective_from=date(2026, 8, 16),
            allotment_basis=ParkingAllotmentBasis.INCLUDED_WITH_UNIT,
            reason="move",
        ),
    )
