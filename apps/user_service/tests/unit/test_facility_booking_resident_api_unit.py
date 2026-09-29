"""Unit tests for resident facility booking API route handlers."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.facility_booking_resident import (
    cancel_reservation,
    create_reservation,
    get_cancel_quote,
    get_day_availability,
    get_facility,
    get_month_availability,
    get_my_invoice,
    get_my_ledger,
    get_my_reservation,
    get_my_reservation_ledger,
    get_my_wallet,
    get_next_availability,
    get_room_availability,
    get_weekly_usage,
    list_facilities,
    list_my_invoices,
    list_my_reservations,
    pay_my_invoice,
    quote_reservation,
    reschedule_reservation,
    top_up_my_wallet,
    validate_reservation,
)
from apps.user_service.app.schemas.enums import FacilityBookingPaymentMethod
from apps.user_service.app.schemas.facility_booking import (
    CancelReservationRequest,
    CreateResidentReservationRequest,
    PayInvoiceRequest,
    RescheduleReservationRequest,
    ResidentReservationDraftRequest,
    ResidentReservationListQuery,
    ResidentWalletTopUpRequest,
)
from apps.user_service.app.utils.common_utils import UserContext

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
FACILITY_ID = "22222222-2222-2222-2222-222222222222"
RESERVATION_ID = "33333333-3333-3333-3333-333333333333"
INVOICE_ID = "44444444-4444-4444-4444-444444444444"
CONTACT_ID = "55555555-5555-5555-5555-555555555555"
LOCAL_DATE = date(2026, 6, 15)


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
            "path": "/resident/facility-bookings",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


def _user_context() -> UserContext:
    return UserContext(
        user_id="resident-1",
        email="resident@example.com",
        organization_id="org-1",
    )


def _access_patch(mock_access):
    mock_access.return_value = (_user_context(), {"id": CONTACT_ID})


def _draft() -> ResidentReservationDraftRequest:
    return ResidentReservationDraftRequest(
        facility_id=FACILITY_ID,
        local_date=LOCAL_DATE,
        start_min=600,
        end_min=660,
    )


def _reservation() -> dict:
    return {"id": RESERVATION_ID, "facility_id": FACILITY_ID}


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.facility_booking_resident.ensure_resident_booking_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.facility_booking_resident.FacilityBookingConfigService")
async def test_resident_facility_catalog_handlers(mock_config_cls, mock_access):
    _access_patch(mock_access)
    config = mock_config_cls.return_value
    config.list_bookable_facilities = AsyncMock(return_value=[{"id": FACILITY_ID}])
    config.get_workspace = AsyncMock(return_value={"facility_id": FACILITY_ID})

    assert (
        await list_facilities(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await get_facility(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.facility_booking_resident.ensure_resident_booking_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.facility_booking_resident.FacilityAvailabilityService")
async def test_resident_availability_handlers(mock_avail_cls, mock_access):
    _access_patch(mock_access)
    service = mock_avail_cls.return_value
    service.availability_day = AsyncMock(return_value={"slots": []})
    service.availability_month = AsyncMock(return_value={"days": []})
    service.availability_month_summary = AsyncMock(return_value={"summary": True})
    service.next_availability = AsyncMock(return_value={"slot": None})
    service.room_availability = AsyncMock(return_value=[])
    service.weekly_usage = AsyncMock(return_value={"used": 0})
    service.evaluate_draft = AsyncMock(return_value={"valid": True})

    assert (
        await get_day_availability(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            local_date=LOCAL_DATE,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await get_month_availability(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            start=LOCAL_DATE,
            summary=False,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await get_month_availability(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            start=LOCAL_DATE,
            summary=True,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await get_next_availability(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await get_room_availability(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            check_in=LOCAL_DATE,
            check_out=date(2026, 6, 17),
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await get_weekly_usage(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            local_date=LOCAL_DATE,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    draft = _draft()
    assert (
        await quote_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            body=draft,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await validate_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            body=draft,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.facility_booking_resident.ensure_resident_booking_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.facility_booking_resident.FacilityReservationService")
async def test_resident_reservation_handlers(mock_res_cls, mock_access):
    _access_patch(mock_access)
    service = mock_res_cls.return_value
    service.create_resident = AsyncMock(return_value=_reservation())
    service.list_mine = AsyncMock(return_value=([], 0))
    service.get_reservation = AsyncMock(return_value=_reservation())
    service.cancel_quote = AsyncMock(return_value={"fee": 0, "refund": 100})
    service.cancel = AsyncMock(return_value=_reservation())
    service.reschedule = AsyncMock(return_value=_reservation())

    draft = CreateResidentReservationRequest(
        facility_id=FACILITY_ID,
        local_date=LOCAL_DATE,
        start_min=600,
        end_min=660,
    )

    assert (
        await create_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            body=draft,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 201

    assert (
        await list_my_reservations(
            request=_request(),
            project_id=PROJECT_ID,
            query=ResidentReservationListQuery(),
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await get_my_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await get_cancel_quote(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await cancel_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            body=CancelReservationRequest(),
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await reschedule_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            body=RescheduleReservationRequest(
                local_date=LOCAL_DATE,
                start_min=720,
                end_min=780,
            ),
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.facility_booking_resident.ensure_resident_booking_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.facility_booking_resident.FacilityBookingLedgerService")
@patch("apps.user_service.app.api.facility_booking_resident.FacilityReservationService")
async def test_resident_ledger_handlers(mock_res_cls, mock_ledger_cls, mock_access):
    _access_patch(mock_access)
    mock_res_cls.return_value.get_reservation = AsyncMock(return_value=_reservation())
    mock_ledger_cls.return_value.statement = AsyncMock(return_value={"balance": 0, "entries": []})
    mock_ledger_cls.return_value.list_reservation = AsyncMock(return_value=[])

    assert (
        await get_my_ledger(
            request=_request(),
            project_id=PROJECT_ID,
            page=1,
            page_size=50,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await get_my_reservation_ledger(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.facility_booking_resident.ensure_resident_booking_access",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.facility_booking_resident.FacilityBookingBillingService")
async def test_resident_billing_handlers(mock_billing_cls, mock_access):
    _access_patch(mock_access)
    billing = mock_billing_cls.return_value
    billing.list_invoices = AsyncMock(return_value=([], 0))
    billing.get_invoice = AsyncMock(return_value={"id": INVOICE_ID})
    billing.pay_invoice = AsyncMock(return_value={"id": INVOICE_ID, "status": "paid"})
    billing.get_wallet = AsyncMock(return_value={"balance": 5000})
    billing.top_up = AsyncMock(return_value={"balance": 6000})

    assert (
        await list_my_invoices(
            request=_request(),
            project_id=PROJECT_ID,
            page=1,
            page_size=50,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await get_my_invoice(
            request=_request(),
            project_id=PROJECT_ID,
            invoice_id=INVOICE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await pay_my_invoice(
            request=_request(),
            project_id=PROJECT_ID,
            invoice_id=INVOICE_ID,
            body=PayInvoiceRequest(method=FacilityBookingPaymentMethod.WALLET),
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await get_my_wallet(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200

    assert (
        await top_up_my_wallet(
            request=_request(),
            project_id=PROJECT_ID,
            body=ResidentWalletTopUpRequest(
                amount=1000,
                method=FacilityBookingPaymentMethod.ONLINE,
            ),
            db_connection=MagicMock(),
            current_user={"sub": "resident-1"},
        )
    ).status_code == 200
