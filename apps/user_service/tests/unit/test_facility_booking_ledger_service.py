"""Unit tests for FacilityBookingLedgerService."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums import FacilityBookingLedgerType
from apps.user_service.app.schemas.facility_booking import RaiseChargeRequest
from apps.user_service.app.services.facility_booking_ledger_service import (
    FacilityBookingLedgerService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ValidationException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
CONTACT_ID = "33333333-3333-3333-3333-333333333333"


def _service() -> FacilityBookingLedgerService:
    svc = FacilityBookingLedgerService(
        db_connection=MagicMock(),
        user_context=UserContext(user_id="user-1", email="a@b.com", organization_id="org-1"),
    )
    svc.repo = MagicMock()
    svc.reservations_repo = MagicMock()
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_project_membership = AsyncMock(return_value=True)
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_net_paid_sums_charge_types_only():
    svc = _service()
    svc.repo.list_for_reservation = AsyncMock(
        return_value=[
            {"entry_type": "charge", "amount": 1000},
            {"entry_type": "refund", "amount": -400},
            {"entry_type": "deposit", "amount": 200},
        ]
    )

    paid = await svc.net_paid_for(project_id=PROJECT_ID, reservation_id="r1")

    assert paid == 1200


@pytest.mark.asyncio
async def test_raise_charge_posts_manual_charge():
    svc = _service()
    svc.repo.insert = AsyncMock(
        return_value={
            "id": "l1",
            "contact_id": CONTACT_ID,
            "reservation_id": None,
            "invoice_id": None,
            "entry_type": FacilityBookingLedgerType.MANUAL_CHARGE.value,
            "description": "Broken racquet",
            "amount": 250,
            "method": None,
            "posted_at": datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
        }
    )

    result = await svc.raise_charge(
        project_id=PROJECT_ID,
        body=RaiseChargeRequest(contact_id=CONTACT_ID, description="Broken racquet", amount=250.4),
    )

    posted = svc.repo.insert.await_args.args[0]
    assert posted["entry_type"] == FacilityBookingLedgerType.MANUAL_CHARGE.value
    assert posted["amount"] == 250
    assert result["amount"] == 250


@pytest.mark.asyncio
async def test_raise_charge_rejects_non_member():
    svc = _service()
    svc.contact_units_repo.contact_has_active_project_membership = AsyncMock(return_value=False)

    with pytest.raises(ValidationException):
        await svc.raise_charge(
            project_id=PROJECT_ID,
            body=RaiseChargeRequest(
                contact_id=CONTACT_ID, description="Broken racquet", amount=100
            ),
        )


@pytest.mark.asyncio
async def test_unbilled_rolls_up_contact_net():
    svc = _service()
    posted = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
    svc.repo.list_unbilled = AsyncMock(
        return_value=[
            {
                "id": "1",
                "contact_id": CONTACT_ID,
                "contact_name": "Ada",
                "reservation_id": "r1",
                "facility_name": "Tennis",
                "invoice_id": None,
                "entry_type": "charge",
                "description": "Tennis — booking",
                "amount": 1000,
                "method": None,
                "posted_at": posted,
            },
            {
                "id": "2",
                "contact_id": CONTACT_ID,
                "contact_name": "Ada",
                "reservation_id": "r1",
                "facility_name": "Tennis",
                "invoice_id": None,
                "entry_type": "refund",
                "description": "Tennis — refund",
                "amount": -400,
                "method": None,
                "posted_at": posted,
            },
        ]
    )

    result = await svc.unbilled(project_id=PROJECT_ID)

    assert result["unbilled_total"] == 600
    assert result["unbilled_contacts"] == 1
    assert result["contacts"][0]["amount"] == 600


@pytest.mark.asyncio
async def test_post_skips_zero_amount():
    svc = _service()
    svc.repo.insert = AsyncMock()

    result = await svc.post(
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        entry_type="charge",
        description="noop",
        amount=0,
    )

    assert result == {}
    svc.repo.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_reservation_and_statement():
    posted = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
    row = {
        "id": "l1",
        "contact_id": CONTACT_ID,
        "contact_name": "Ada",
        "reservation_id": "r1",
        "facility_name": "Tennis",
        "invoice_id": None,
        "entry_type": "charge",
        "description": "Booking",
        "amount": 100,
        "method": None,
        "posted_at": posted,
    }
    svc = _service()
    svc.repo.list_for_reservation = AsyncMock(return_value=[row])
    svc.repo.list_for_contact = AsyncMock(return_value=([row], 1))
    svc.repo.contact_balance = AsyncMock(return_value=100)

    reservation_entries = await svc.list_reservation(
        project_id=PROJECT_ID,
        reservation_id="r1",
    )
    assert reservation_entries[0]["amount"] == 100

    statement = await svc.statement(
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
    )
    assert statement["balance"] == 100
    assert len(statement["entries"]) == 1


@pytest.mark.asyncio
async def test_list_project_returns_total():
    svc = _service()
    svc.repo.list_project = AsyncMock(return_value=([], 0))

    entries, total = await svc.list_project(project_id=PROJECT_ID)

    assert entries == []
    assert total == 0


@pytest.mark.asyncio
async def test_raise_charge_reservation_not_found():
    from libs.shared_utils.http_exceptions import NotFoundException

    svc = _service()
    svc.reservations_repo.get_reservation = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await svc.raise_charge(
            project_id=PROJECT_ID,
            body=RaiseChargeRequest(
                contact_id=CONTACT_ID,
                description="Late fee",
                amount=100,
                reservation_id="missing-reservation",
            ),
        )


@pytest.mark.asyncio
async def test_raise_charge_rejects_non_positive_amount():
    svc = _service()

    with (
        patch(
            "apps.user_service.app.services.facility_booking_ledger_service.ts_round",
            return_value=0,
        ),
        pytest.raises(ValidationException),
    ):
        await svc.raise_charge(
            project_id=PROJECT_ID,
            body=RaiseChargeRequest(
                contact_id=CONTACT_ID,
                description="Invalid",
                amount=0.4,
            ),
        )


@pytest.mark.asyncio
async def test_unbilled_picks_oldest_posted_at():
    older = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
    svc = _service()
    svc.repo.list_unbilled = AsyncMock(
        return_value=[
            {
                "id": "1",
                "contact_id": CONTACT_ID,
                "contact_name": None,
                "reservation_id": "r1",
                "facility_name": "Tennis",
                "invoice_id": None,
                "entry_type": "charge",
                "description": "Newer",
                "amount": 100,
                "method": None,
                "posted_at": newer,
            },
            {
                "id": "2",
                "contact_id": CONTACT_ID,
                "contact_name": "Ada",
                "reservation_id": "r1",
                "facility_name": "Tennis",
                "invoice_id": None,
                "entry_type": "charge",
                "description": "Older",
                "amount": 50,
                "method": None,
                "posted_at": older,
            },
        ]
    )

    result = await svc.unbilled(project_id=PROJECT_ID)

    assert str(result["contacts"][0]["oldest_at"]).startswith("2026-09-20")
