"""Unit tests for staff facility booking admin API route handlers."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.facility_booking_admin import (
    adjust_wallet,
    admin_cancel_reservation,
    admin_reschedule_reservation,
    approve_reservation,
    check_in_reservation,
    complete_reservation,
    create_booking_unit,
    create_closure,
    create_maintenance,
    create_schedule,
    create_slot_block,
    create_staff_reservation,
    delete_booking_unit,
    delete_closure,
    delete_maintenance,
    delete_schedule,
    delete_slot_block,
    delete_staff_assignment,
    generate_invoices,
    get_contact_ledger,
    get_day_availability,
    get_facility_workspace,
    get_invoice,
    get_month_availability,
    get_next_availability,
    get_reservation,
    get_reservation_ledger,
    get_room_availability,
    get_settings,
    get_wallet,
    list_bookable_facilities,
    list_invoices,
    list_ledger,
    list_pending_reservations,
    list_reservations,
    list_staff_assignments,
    list_unbilled,
    no_show_reservation,
    pay_invoice,
    payments_overview,
    quote_staff_reservation,
    raise_charge,
    reject_reservation,
    set_wallet_limit,
    top_up_wallet,
    update_booking_unit,
    update_facility_config,
    update_schedule,
    update_settings,
    upsert_staff_assignment,
    add_reservation_note,
)
from apps.user_service.app.schemas.enums import FacilityBookingPaymentMethod
from apps.user_service.app.schemas.facility_booking import (
    CancelReservationRequest,
    CreateStaffReservationRequest,
    GenerateInvoicesRequest,
    PayInvoiceRequest,
    RaiseChargeRequest,
    RejectReservationRequest,
    ReservationNoteRequest,
    RescheduleReservationRequest,
    StaffReservationDraftRequest,
    StaffReservationListQuery,
    WalletAdjustRequest,
    WalletLimitRequest,
    WalletTopUpRequest,
)
from apps.user_service.app.schemas.facility_booking_config import DayHours, UpdateFacilityBookingConfigRequest
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
from apps.user_service.app.utils.common_utils import UserContext

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
FACILITY_ID = "22222222-2222-2222-2222-222222222222"
UNIT_ID = "33333333-3333-3333-3333-333333333333"
RESERVATION_ID = "44444444-4444-4444-4444-444444444444"
CONTACT_ID = "55555555-5555-5555-5555-555555555555"
INVOICE_ID = "66666666-6666-6666-6666-666666666666"
PERIOD_ID = "77777777-7777-7777-7777-777777777777"
BLOCK_ID = "88888888-8888-8888-8888-888888888888"
CLOSURE_ID = "99999999-9999-9999-9999-999999999999"
WINDOW_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ASSIGNMENT_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
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
            "path": "/facility-bookings",
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


def _staff_patch(mock_staff):
    mock_staff.return_value = _user_context()


def _closed_week() -> list[DayHours]:
    return [DayHours(open=0, close=60, closed=True) for _ in range(7)]


def _reservation() -> dict:
    return {"id": RESERVATION_ID, "facility_id": FACILITY_ID}


def _workspace() -> dict:
    return {"facility_id": FACILITY_ID, "config": {"version": 1}}


@pytest.mark.asyncio
@patch("apps.user_service.app.api.facility_booking_admin.ensure_booking_staff", new_callable=AsyncMock)
@patch("apps.user_service.app.api.facility_booking_admin.FacilityBookingConfigService")
async def test_admin_config_and_inventory_handlers(mock_config_cls, mock_staff):
    _staff_patch(mock_staff)
    config = mock_config_cls.return_value
    config.list_bookable_facilities = AsyncMock(return_value=[{"id": FACILITY_ID}])
    config.get_workspace = AsyncMock(return_value=_workspace())
    config.update_config = AsyncMock(return_value=_workspace())
    config.create_unit = AsyncMock(return_value={"id": UNIT_ID})
    config.update_unit = AsyncMock(return_value={"id": UNIT_ID})
    config.delete_inventory = AsyncMock(return_value=None)
    config.create_schedule = AsyncMock(return_value={"id": PERIOD_ID})
    config.update_schedule = AsyncMock(return_value={"id": PERIOD_ID})
    config.create_slot_block = AsyncMock(return_value={"id": BLOCK_ID})
    config.create_closure = AsyncMock(return_value={"id": CLOSURE_ID})
    config.create_maintenance = AsyncMock(return_value={"id": WINDOW_ID})

    assert (
        await list_bookable_facilities(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await get_facility_workspace(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await update_facility_config(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=UpdateFacilityBookingConfigRequest(version=1, accepting_bookings=True),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await create_booking_unit(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=CreateBookingUnitRequest(name="Court 1"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201

    assert (
        await update_booking_unit(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            unit_id=UNIT_ID,
            body=UpdateBookingUnitRequest(name="Court 1A"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await delete_booking_unit(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            unit_id=UNIT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    schedule_body = CreateSchedulePeriodRequest(
        name="Summer",
        starts_on=LOCAL_DATE,
        ends_on=LOCAL_DATE,
        hours=_closed_week(),
    )
    assert (
        await create_schedule(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=schedule_body,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201

    assert (
        await update_schedule(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            period_id=PERIOD_ID,
            body=UpdateSchedulePeriodRequest(name="Summer peak"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await delete_schedule(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            period_id=PERIOD_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await create_slot_block(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=CreateSlotBlockRequest(
                starts_on=LOCAL_DATE,
                reason="Private event",
            ),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201

    assert (
        await delete_slot_block(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            block_id=BLOCK_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await create_closure(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=CreateClosureRequest(closed_on=LOCAL_DATE, reason="Holiday"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201

    assert (
        await delete_closure(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            closure_id=CLOSURE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await create_maintenance(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            body=CreateMaintenanceWindowRequest(
                on_date=LOCAL_DATE,
                from_min=600,
                to_min=720,
                note="Resurfacing",
            ),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201

    assert (
        await delete_maintenance(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            window_id=WINDOW_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.facility_booking_admin.ensure_booking_staff", new_callable=AsyncMock)
@patch("apps.user_service.app.api.facility_booking_admin.FacilityAvailabilityService")
async def test_admin_availability_handlers(mock_avail_cls, mock_staff):
    _staff_patch(mock_staff)
    service = mock_avail_cls.return_value
    service.availability_day = AsyncMock(return_value={"slots": []})
    service.availability_month = AsyncMock(return_value={"days": []})
    service.availability_month_summary = AsyncMock(return_value={"summary": True})
    service.next_availability = AsyncMock(return_value=None)
    service.room_availability = AsyncMock(return_value=[])
    service.evaluate_draft = AsyncMock(return_value={"valid": True, "quote": 100})

    assert (
        await get_day_availability(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            local_date=LOCAL_DATE,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
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
            current_user={"sub": "staff-1"},
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
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await get_next_availability(
            request=_request(),
            project_id=PROJECT_ID,
            facility_id=FACILITY_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
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
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    draft = StaffReservationDraftRequest(
        facility_id=FACILITY_ID,
        host_contact_id=CONTACT_ID,
        local_date=LOCAL_DATE,
        start_min=600,
        end_min=660,
    )
    assert (
        await quote_staff_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            body=draft,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.facility_booking_admin.ensure_can_operate_facility", new_callable=AsyncMock)
@patch("apps.user_service.app.api.facility_booking_admin.ensure_booking_staff", new_callable=AsyncMock)
@patch("apps.user_service.app.api.facility_booking_admin.FacilityReservationService")
async def test_admin_reservation_handlers(mock_res_cls, mock_staff, mock_operate):
    _staff_patch(mock_staff)
    mock_operate.return_value = None
    service = mock_res_cls.return_value
    service.create_staff = AsyncMock(return_value=_reservation())
    service.list_reservations = AsyncMock(return_value=([], 0))
    service.get_reservation = AsyncMock(return_value=_reservation())
    service.approve = AsyncMock(return_value=_reservation())
    service.reject = AsyncMock(return_value=_reservation())
    service.check_in = AsyncMock(return_value=_reservation())
    service.complete = AsyncMock(return_value=_reservation())
    service.mark_no_show = AsyncMock(return_value=_reservation())
    service.cancel = AsyncMock(return_value=_reservation())
    service.reschedule = AsyncMock(return_value=_reservation())
    service.add_note = AsyncMock(return_value=_reservation())

    create_body = CreateStaffReservationRequest(
        facility_id=FACILITY_ID,
        host_contact_id=CONTACT_ID,
        local_date=LOCAL_DATE,
        start_min=600,
        end_min=660,
    )
    assert (
        await create_staff_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            body=create_body,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201

    assert (
        await list_reservations(
            request=_request(),
            project_id=PROJECT_ID,
            query=StaffReservationListQuery(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await list_pending_reservations(
            request=_request(),
            project_id=PROJECT_ID,
            page=1,
            page_size=20,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await get_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await approve_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await reject_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            body=RejectReservationRequest(reason="Over capacity"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await check_in_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await complete_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await no_show_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await admin_cancel_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            body=CancelReservationRequest(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await admin_reschedule_reservation(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            body=RescheduleReservationRequest(
                local_date=LOCAL_DATE,
                start_min=720,
                end_min=780,
            ),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await add_reservation_note(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            body=ReservationNoteRequest(message="VIP guest"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.facility_booking_admin.ensure_booking_staff", new_callable=AsyncMock)
@patch("apps.user_service.app.api.facility_booking_admin.FacilityBookingConfigService")
async def test_admin_staff_assignments_and_settings(mock_config_cls, mock_staff):
    _staff_patch(mock_staff)
    config = mock_config_cls.return_value
    config.list_staff_assignments = AsyncMock(return_value=[{"id": ASSIGNMENT_ID}])
    config.upsert_staff_assignment = AsyncMock(return_value={"id": ASSIGNMENT_ID})
    config.delete_staff_assignment = AsyncMock(return_value=None)
    config.get_settings = AsyncMock(return_value={"timezone": "Asia/Kolkata"})
    config.update_settings = AsyncMock(return_value={"timezone": "Asia/Kolkata"})

    assert (
        await list_staff_assignments(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await upsert_staff_assignment(
            request=_request(),
            project_id=PROJECT_ID,
            body=UpsertStaffAssignmentRequest(
                project_member_id="member-1",
                facility_ids=[FACILITY_ID],
            ),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await delete_staff_assignment(
            request=_request(),
            project_id=PROJECT_ID,
            assignment_id=ASSIGNMENT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await get_settings(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await update_settings(
            request=_request(),
            project_id=PROJECT_ID,
            body=UpdateProjectBookingSettingsRequest(timezone="Asia/Kolkata"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.facility_booking_admin.ensure_booking_staff", new_callable=AsyncMock)
@patch("apps.user_service.app.api.facility_booking_admin.FacilityBookingNotificationService")
@patch("apps.user_service.app.api.facility_booking_admin.FacilityBookingLedgerService")
async def test_admin_ledger_handlers(mock_ledger_cls, mock_notify_cls, mock_staff):
    _staff_patch(mock_staff)
    ledger = mock_ledger_cls.return_value
    ledger.list_project = AsyncMock(return_value=([], 0))
    ledger.unbilled = AsyncMock(return_value={"unbilled_total": 0})
    ledger.statement = AsyncMock(return_value={"balance": 0})
    ledger.list_reservation = AsyncMock(return_value=[])
    ledger.raise_charge = AsyncMock(return_value={"id": "le-1", "amount": 500})
    mock_notify_cls.return_value.charge_posted = AsyncMock(return_value=None)

    assert (
        await list_ledger(
            request=_request(),
            project_id=PROJECT_ID,
            page=1,
            page_size=50,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await list_unbilled(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await get_contact_ledger(
            request=_request(),
            project_id=PROJECT_ID,
            contact_id=CONTACT_ID,
            page=1,
            page_size=50,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await get_reservation_ledger(
            request=_request(),
            project_id=PROJECT_ID,
            reservation_id=RESERVATION_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await raise_charge(
            request=_request(),
            project_id=PROJECT_ID,
            body=RaiseChargeRequest(
                contact_id=CONTACT_ID,
                description="Late fee charge",
                amount=500.0,
            ),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201


@pytest.mark.asyncio
@patch("apps.user_service.app.api.facility_booking_admin.ensure_booking_staff", new_callable=AsyncMock)
@patch("apps.user_service.app.api.facility_booking_admin.FacilityBookingBillingService")
async def test_admin_billing_and_wallet_handlers(mock_billing_cls, mock_staff):
    _staff_patch(mock_staff)
    billing = mock_billing_cls.return_value
    billing.overview = AsyncMock(return_value={"unbilled_total": 0})
    billing.list_invoices = AsyncMock(return_value=([], 0))
    billing.generate_invoices = AsyncMock(return_value={"created": 1})
    billing.get_invoice = AsyncMock(return_value={"id": INVOICE_ID})
    billing.pay_invoice = AsyncMock(return_value={"id": INVOICE_ID})
    billing.get_wallet = AsyncMock(return_value={"balance": 1000})
    billing.top_up = AsyncMock(return_value={"balance": 2000})
    billing.adjust = AsyncMock(return_value={"balance": 1500})
    billing.set_limit = AsyncMock(return_value={"credit_limit": 5000})

    assert (
        await payments_overview(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await list_invoices(
            request=_request(),
            project_id=PROJECT_ID,
            page=1,
            page_size=50,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await generate_invoices(
            request=_request(),
            project_id=PROJECT_ID,
            body=GenerateInvoicesRequest(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201

    assert (
        await get_invoice(
            request=_request(),
            project_id=PROJECT_ID,
            invoice_id=INVOICE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await pay_invoice(
            request=_request(),
            project_id=PROJECT_ID,
            invoice_id=INVOICE_ID,
            body=PayInvoiceRequest(method=FacilityBookingPaymentMethod.CASH),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await get_wallet(
            request=_request(),
            project_id=PROJECT_ID,
            contact_id=CONTACT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await top_up_wallet(
            request=_request(),
            project_id=PROJECT_ID,
            body=WalletTopUpRequest(
                contact_id=CONTACT_ID,
                amount=1000,
                method=FacilityBookingPaymentMethod.ONLINE,
            ),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await adjust_wallet(
            request=_request(),
            project_id=PROJECT_ID,
            body=WalletAdjustRequest(
                contact_id=CONTACT_ID,
                amount=-100,
                reason="Correction",
            ),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200

    assert (
        await set_wallet_limit(
            request=_request(),
            project_id=PROJECT_ID,
            contact_id=CONTACT_ID,
            body=WalletLimitRequest(limit=5000),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
