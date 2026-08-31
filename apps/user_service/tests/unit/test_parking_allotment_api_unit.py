"""Unit tests for parking allotment API route handlers."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.parking_allotment import (
    allot_parking_slot_to_unit,
    get_parking_allotment_summary,
    get_parking_allotment_unit,
    list_parking_allotment_slots,
)
from apps.user_service.app.schemas.enums import (
    ParkingAllotmentBasis,
    ParkingFacilitySubtype,
    ParkingSlotDisplayStatus,
    ParkingVehicleCategory,
)
from apps.user_service.app.schemas.parking_allotment import (
    AllotParkingSlotRequest,
    ParkingAllotmentSlotDetailResponse,
    ParkingAllotmentSlotListQuery,
    ParkingAllotmentSummaryQuery,
    ParkingAllotmentSummaryResponse,
    ParkingAllotmentUnitListItemResponse,
)
from apps.user_service.app.utils.common_utils import UserContext

PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
SLOT_ID = "880e8400-e29b-41d4-a716-446655440003"
UNIT_ID = "770e8400-e29b-41d4-a716-446655440002"


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/projects/test/parking-allotment/slots/allot",
            "headers": [(b"lan", b"en")],
            "client": ("127.0.0.1", 50000),
            "query_string": b"",
        }
    )


def _user_context() -> UserContext:
    return UserContext(
        user_id="staff-1",
        email="staff@example.com",
        organization_id="org-1",
    )


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.parking_allotment.ensure_staff_project_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.parking_allotment.ParkingAllotmentService")
async def test_get_parking_allotment_summary(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.get_summary = AsyncMock(
        return_value=ParkingAllotmentSummaryResponse(
            total_slots=106,
            allotted=60,
            free_to_allot=33,
            visitor_pool=9,
            blocked=4,
            units_short_of_entitlement=30,
        )
    )

    response = await get_parking_allotment_summary(
        request=_request(),
        project_id=PROJECT_ID,
        query=ParkingAllotmentSummaryQuery(),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.parking_allotment.ensure_staff_project_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.parking_allotment.ParkingAllotmentService")
async def test_list_parking_allotment_slots(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.list_slots = AsyncMock(return_value=([], 0))

    response = await list_parking_allotment_slots(
        request=_request(),
        project_id=PROJECT_ID,
        query=ParkingAllotmentSlotListQuery(),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.parking_allotment.ensure_staff_project_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.parking_allotment.ParkingAllotmentService")
async def test_get_parking_allotment_unit(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.get_unit = AsyncMock(
        return_value=ParkingAllotmentUnitListItemResponse(
            id=UNIT_ID,
            code="A-1804",
            configuration_label="3 BHK",
            two_wheeler_parking_entitlement=1,
            four_wheeler_parking_entitlement=1,
            slots_assigned=1,
            entitlement_status="short",
            entitlement_short_by=1,
            slots_held=[],
            allowed_actions=["allot_slot"],
        )
    )

    response = await get_parking_allotment_unit(
        request=_request(),
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.parking_allotment.ensure_staff_project_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.parking_allotment.ParkingAllotmentService")
async def test_allot_parking_slot_to_unit(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.allot_slot = AsyncMock(
        return_value=ParkingAllotmentSlotDetailResponse(
            id=SLOT_ID,
            slot_code="A-B2-002",
            slot_code_label="A-B2-002",
            level_label="B2",
            bay_label="Bay B",
            slot_type=ParkingFacilitySubtype.BASEMENT,
            slot_type_label="Basement",
            parking_vehicle_category=ParkingVehicleCategory.FOUR_WHEELER,
            status=ParkingSlotDisplayStatus.ALLOTTED,
            facility_id="facility-1",
            slot_number=2,
        )
    )

    response = await allot_parking_slot_to_unit(
        request=_request(),
        project_id=PROJECT_ID,
        slot_id=SLOT_ID,
        body=AllotParkingSlotRequest(
            unit_id="unit-1",
            effective_from=date(2026, 8, 16),
            allotment_basis=ParkingAllotmentBasis.INCLUDED_WITH_UNIT,
        ),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )
    assert response.status_code == 200
