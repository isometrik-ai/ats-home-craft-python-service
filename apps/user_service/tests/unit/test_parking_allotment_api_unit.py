"""Unit tests for parking allotment admin API route handlers."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.parking_allotment import (
    allot_parking_slot_from_unit,
    allot_parking_slot_to_unit,
    block_parking_slot,
    get_parking_allotment_slot_detail,
    get_parking_allotment_summary,
    get_parking_allotment_unit,
    list_parking_allotment_slot_history,
    list_parking_allotment_slots,
    list_parking_allotment_units,
    reassign_parking_slot,
    release_parking_slot,
    unblock_parking_slot,
)
from apps.user_service.app.schemas.parking_allotment import (
    AllotParkingSlotRequest,
    BlockParkingSlotRequest,
    ParkingAllotmentSlotListQuery,
    ParkingAllotmentSummaryQuery,
    ParkingAllotmentUnitListQuery,
    ReassignParkingSlotRequest,
    ReleaseParkingSlotRequest,
    UnitAllotParkingSlotRequest,
)
from apps.user_service.app.utils.common_utils import UserContext

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
SLOT_ID = "22222222-2222-2222-2222-222222222222"
UNIT_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def _skip_audit_logging():
    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_decorator._log_audit_event",
        new_callable=AsyncMock,
    ):
        yield


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/parking-allotment",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


def _user_context() -> UserContext:
    return UserContext(
        user_id="staff-1",
        email="staff@example.com",
        organization_id="org-1",
    )


def _slot_mutation():
    return MagicMock(model_dump=lambda: {"id": SLOT_ID, "slot_code": "P-101"})


def _summary():
    return MagicMock(
        model_dump=lambda: {
            "total_slots": 10,
            "allotted": 4,
            "free_to_allot": 5,
            "visitor_pool": 0,
            "blocked": 1,
            "units_short_of_entitlement": 2,
        }
    )


def _list_item():
    return MagicMock(model_dump=lambda: {"id": SLOT_ID, "slot_code": "P-101"})


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.parking_allotment.ensure_staff_project_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.parking_allotment.ParkingAllotmentService")
async def test_parking_allotment_read_handlers(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.get_summary = AsyncMock(return_value=_summary())
    service.list_slots = AsyncMock(return_value=([_list_item()], 1))
    service.get_slot_detail = AsyncMock(return_value=_slot_mutation())
    service.list_slot_history = AsyncMock(
        return_value=[MagicMock(model_dump=lambda: {"id": "evt-1"})]
    )
    service.list_units = AsyncMock(return_value=([_list_item()], 1))
    service.get_unit = AsyncMock(return_value=_list_item())

    assert (
        await get_parking_allotment_summary(
            request=_request(),
            project_id=PROJECT_ID,
            query=ParkingAllotmentSummaryQuery(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await list_parking_allotment_slots(
            request=_request(),
            project_id=PROJECT_ID,
            query=ParkingAllotmentSlotListQuery(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await get_parking_allotment_slot_detail(
            request=_request(),
            project_id=PROJECT_ID,
            slot_id=SLOT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await list_parking_allotment_slot_history(
            request=_request(),
            project_id=PROJECT_ID,
            slot_id=SLOT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await list_parking_allotment_units(
            request=_request(),
            project_id=PROJECT_ID,
            query=ParkingAllotmentUnitListQuery(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await get_parking_allotment_unit(
            request=_request(),
            project_id=PROJECT_ID,
            unit_id=UNIT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.parking_allotment.ensure_staff_project_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.parking_allotment.ParkingAllotmentService")
async def test_parking_allotment_mutation_handlers(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    mutation = _slot_mutation()
    service.allot_slot = AsyncMock(return_value=mutation)
    service.reassign_slot = AsyncMock(return_value=mutation)
    service.release_slot = AsyncMock(return_value=mutation)
    service.block_slot = AsyncMock(return_value=mutation)
    service.unblock_slot = AsyncMock(return_value=mutation)
    service.allot_slot_to_unit = AsyncMock(return_value=mutation)

    effective = date(2026, 4, 1)

    assert (
        await allot_parking_slot_to_unit(
            request=_request(),
            project_id=PROJECT_ID,
            slot_id=SLOT_ID,
            body=AllotParkingSlotRequest(unit_id=UNIT_ID, effective_from=effective),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await reassign_parking_slot(
            request=_request(),
            project_id=PROJECT_ID,
            slot_id=SLOT_ID,
            body=ReassignParkingSlotRequest(unit_id=UNIT_ID, effective_from=effective),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await release_parking_slot(
            request=_request(),
            project_id=PROJECT_ID,
            slot_id=SLOT_ID,
            body=None,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await release_parking_slot(
            request=_request(),
            project_id=PROJECT_ID,
            slot_id=SLOT_ID,
            body=ReleaseParkingSlotRequest(reason="tenant moved out"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await block_parking_slot(
            request=_request(),
            project_id=PROJECT_ID,
            slot_id=SLOT_ID,
            body=BlockParkingSlotRequest(reason="maintenance"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await unblock_parking_slot(
            request=_request(),
            project_id=PROJECT_ID,
            slot_id=SLOT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await allot_parking_slot_from_unit(
            request=_request(),
            project_id=PROJECT_ID,
            unit_id=UNIT_ID,
            body=UnitAllotParkingSlotRequest(slot_id=SLOT_ID, effective_from=effective),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
