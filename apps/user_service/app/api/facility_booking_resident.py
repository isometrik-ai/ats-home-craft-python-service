"""Resident facility booking API (project-scoped)."""

from __future__ import annotations

from datetime import date

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.enums import (
    FacilityBookingPaymentMethod,
    FacilityReservationActorType,
)
from apps.user_service.app.schemas.facility_booking import (
    BookableFacilityListApiResponse,
    BookingInvoiceApiResponse,
    BookingInvoiceListApiResponse,
    CancelQuoteApiResponse,
    CancelReservationRequest,
    CreateResidentReservationRequest,
    DayAvailabilityApiResponse,
    DraftEvaluationApiResponse,
    FacilityBookingWorkspaceApiResponse,
    FacilityReservationApiResponse,
    FacilityReservationListApiResponse,
    LedgerEntryListApiResponse,
    LedgerStatementApiResponse,
    MonthOverviewListApiResponse,
    NextAvailabilityApiResponse,
    PayInvoiceRequest,
    RescheduleReservationRequest,
    ResidentReservationDraftRequest,
    ResidentReservationListQuery,
    ResidentWalletTopUpRequest,
    RoomAvailabilityListApiResponse,
    WalletApiResponse,
    WalletTopUpRequest,
    WeeklyUsageApiResponse,
)
from apps.user_service.app.services.facility_availability_service import (
    FacilityAvailabilityService,
)
from apps.user_service.app.services.facility_booking_billing_service import (
    FacilityBookingBillingService,
)
from apps.user_service.app.services.facility_booking_config_service import (
    FacilityBookingConfigService,
)
from apps.user_service.app.services.facility_booking_ledger_service import (
    FacilityBookingLedgerService,
)
from apps.user_service.app.services.facility_reservation_service import (
    FacilityReservationService,
)
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from apps.user_service.app.utils.facility_booking_access import (
    ensure_resident_booking_access,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(
    prefix="/projects/{project_id}/resident/facility-bookings",
    tags=["Facility Booking (Resident)"],
)

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


def _ok_response(
    model: type,
    description: str,
    *,
    status_code: int = http_status.HTTP_200_OK,
) -> dict[int | str, dict]:
    """Build OpenAPI responses for a successful JSON envelope."""
    return {
        **COMMON_ERROR_RESPONSES,
        status_code: {"model": model, "description": description},
    }


def _created_response(model: type, description: str) -> dict[int | str, dict]:
    """Build OpenAPI responses for HTTP 201 success."""
    return _ok_response(model, description, status_code=http_status.HTTP_201_CREATED)


FACILITIES_LIST_SUCCESS_RESPONSES = _ok_response(
    BookableFacilityListApiResponse,
    "Bookable facilities visible to residents.",
)
WORKSPACE_SUCCESS_RESPONSES = _ok_response(
    FacilityBookingWorkspaceApiResponse,
    "Booking config and inventory for one facility.",
)
DAY_AVAILABILITY_SUCCESS_RESPONSES = _ok_response(
    DayAvailabilityApiResponse,
    "Slot availability for one calendar day.",
)
MONTH_OVERVIEW_SUCCESS_RESPONSES = _ok_response(
    MonthOverviewListApiResponse,
    "Month calendar overview, or slot summary when summary=true.",
)
NEXT_AVAILABILITY_SUCCESS_RESPONSES = _ok_response(
    NextAvailabilityApiResponse,
    "Next bookable slot, or null when none exists.",
)
ROOM_AVAILABILITY_SUCCESS_RESPONSES = _ok_response(
    RoomAvailabilityListApiResponse,
    "Room availability for a stay window.",
)
WEEKLY_USAGE_SUCCESS_RESPONSES = _ok_response(
    WeeklyUsageApiResponse,
    "Weekly booking usage against the facility cap.",
)
DRAFT_EVALUATION_SUCCESS_RESPONSES = _ok_response(
    DraftEvaluationApiResponse,
    "Draft validation result with optional price quote.",
)
RESERVATION_CREATED_RESPONSES = _created_response(
    FacilityReservationApiResponse,
    "Reservation created.",
)
RESERVATION_SUCCESS_RESPONSES = _ok_response(
    FacilityReservationApiResponse,
    "Reservation detail or lifecycle action result.",
)
RESERVATION_LIST_SUCCESS_RESPONSES = _ok_response(
    FacilityReservationListApiResponse,
    "Paginated reservation list for the signed-in resident.",
)
LEDGER_STATEMENT_SUCCESS_RESPONSES = _ok_response(
    LedgerStatementApiResponse,
    "Resident booking ledger statement with balance.",
)
LEDGER_ENTRY_LIST_SUCCESS_RESPONSES = _ok_response(
    LedgerEntryListApiResponse,
    "Ledger entries for one reservation.",
)
CANCEL_QUOTE_SUCCESS_RESPONSES = _ok_response(
    CancelQuoteApiResponse,
    "Cancellation fee and refund preview.",
)
INVOICE_LIST_SUCCESS_RESPONSES = _ok_response(
    BookingInvoiceListApiResponse,
    "Paginated facility booking invoices for the resident.",
)
INVOICE_SUCCESS_RESPONSES = _ok_response(
    BookingInvoiceApiResponse,
    "Invoice detail or payment result.",
)
WALLET_SUCCESS_RESPONSES = _ok_response(
    WalletApiResponse,
    "Resident booking wallet with recent transactions.",
)


def _ok(request, message_key, data, *, created=False):
    """Wrap a success or created response for resident facility booking routes."""
    return success_response(
        request=request,
        message_key=message_key,
        custom_code=CustomStatusCode.CREATED if created else CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_201_CREATED if created else http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("list resident bookable facilities")
@router.get(
    "/facilities",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=FACILITIES_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_facilities(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List bookable facilities visible to residents."""
    user_context, _ = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    items = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).list_bookable_facilities(project_id=project_id, resident_visible_only=True)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="facility_booking.success.facilities_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get resident facility booking config")
@router.get(
    "/facilities/{facility_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=WORKSPACE_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_facility(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return booking config and inventory for one facility."""
    user_context, _ = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).get_workspace(project_id=project_id, facility_id=facility_id)
    return _ok(request, "facility_booking.success.config_retrieved", data)


@handle_api_exceptions("get resident day availability")
@router.get(
    "/facilities/{facility_id}/availability/day",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=DAY_AVAILABILITY_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_day_availability(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    local_date: date = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return slot availability for one calendar day."""
    user_context, _ = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityAvailabilityService(
        db_connection=db_connection, user_context=user_context
    ).availability_day(project_id=project_id, facility_id=facility_id, local_date=local_date)
    return _ok(request, "facility_booking.success.availability_retrieved", data)


@handle_api_exceptions("get resident month availability")
@router.get(
    "/facilities/{facility_id}/availability/month",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=MONTH_OVERVIEW_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_month_availability(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    start: date = Query(...),
    summary: bool = Query(default=False),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return month overview or summary for a facility."""
    user_context, _ = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    service = FacilityAvailabilityService(db_connection=db_connection, user_context=user_context)
    data = (
        await service.availability_month_summary(
            project_id=project_id, facility_id=facility_id, start=start
        )
        if summary
        else await service.availability_month(
            project_id=project_id, facility_id=facility_id, start=start
        )
    )
    return _ok(request, "facility_booking.success.availability_retrieved", data)


@handle_api_exceptions("get resident next availability")
@router.get(
    "/facilities/{facility_id}/availability/next",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=NEXT_AVAILABILITY_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_next_availability(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the next bookable slot when one exists."""
    user_context, _ = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityAvailabilityService(
        db_connection=db_connection, user_context=user_context
    ).next_availability(project_id=project_id, facility_id=facility_id)
    return _ok(request, "facility_booking.success.availability_retrieved", data)


@handle_api_exceptions("get resident room availability")
@router.get(
    "/facilities/{facility_id}/room-availability",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=ROOM_AVAILABILITY_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_room_availability(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    check_in: date = Query(...),
    check_out: date = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return room availability for a stay window."""
    user_context, _ = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityAvailabilityService(
        db_connection=db_connection, user_context=user_context
    ).room_availability(
        project_id=project_id, facility_id=facility_id, check_in=check_in, check_out=check_out
    )
    return _ok(request, "facility_booking.success.availability_retrieved", data)


@handle_api_exceptions("get resident weekly usage")
@router.get(
    "/facilities/{facility_id}/weekly-usage",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=WEEKLY_USAGE_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_weekly_usage(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    local_date: date = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return weekly booking usage against the facility cap."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityAvailabilityService(
        db_connection=db_connection, user_context=user_context
    ).weekly_usage(
        project_id=project_id,
        facility_id=facility_id,
        contact_id=str(contact["id"]),
        local_date=local_date,
    )
    return _ok(request, "facility_booking.success.usage_retrieved", data)


@handle_api_exceptions("quote resident reservation")
@router.post(
    "/quote",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=DRAFT_EVALUATION_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def quote_reservation(
    request: Request,
    project_id: str = Path(...),
    body: ResidentReservationDraftRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Validate a draft and return price quote preview."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityAvailabilityService(
        db_connection=db_connection, user_context=user_context
    ).evaluate_draft(project_id=project_id, body=body, host_contact_id=str(contact["id"]))
    return _ok(request, "facility_booking.success.quote_retrieved", data)


@handle_api_exceptions("validate resident reservation")
@router.post(
    "/validate",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=DRAFT_EVALUATION_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def validate_reservation(
    request: Request,
    project_id: str = Path(...),
    body: ResidentReservationDraftRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Validate a reservation draft without booking."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityAvailabilityService(
        db_connection=db_connection, user_context=user_context
    ).evaluate_draft(project_id=project_id, body=body, host_contact_id=str(contact["id"]))
    return _ok(request, "facility_booking.success.draft_validated", data)


@handle_api_exceptions("create resident reservation")
@router.post(
    "/reservations",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=RESERVATION_CREATED_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="facility_reservations",
    category="FACILITY_BOOKING",
)
async def create_reservation(
    request: Request,
    project_id: str = Path(...),
    body: CreateResidentReservationRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create a facility reservation for the signed-in resident."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).create_resident(project_id=project_id, contact_id=str(contact["id"]), body=body)
    set_audit_context(
        request,
        user_context,
        project_id=project_id,
        table="facility_reservations",
        requested_id=str(data.get("id", "")),
        description="Resident created a facility reservation",
        new_data=data,
    )
    return _ok(request, "facility_booking.success.reservation_created", data, created=True)


@handle_api_exceptions("list my facility reservations")
@router.get(
    "/reservations/mine",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_my_reservations(
    request: Request,
    project_id: str = Path(...),
    query: ResidentReservationListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List the resident's upcoming or past reservations."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    items, total = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).list_mine(
        project_id=project_id,
        contact_id=str(contact["id"]),
        tab=query.tab,
        facility_id=query.facility_id,
        page=query.page,
        page_size=query.page_size,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="facility_booking.success.reservations_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get my facility reservation")
@router.get(
    "/reservations/{reservation_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_my_reservation(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one of the resident's reservations."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).get_reservation(
        project_id=project_id,
        reservation_id=reservation_id,
        include_events=True,
        host_contact_id=str(contact["id"]),
    )
    return _ok(request, "facility_booking.success.reservation_retrieved", data)


@handle_api_exceptions("get my facility booking ledger")
@router.get(
    "/ledger",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=LEDGER_STATEMENT_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_my_ledger(
    request: Request,
    project_id: str = Path(...),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the resident's booking ledger statement."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityBookingLedgerService(
        db_connection=db_connection, user_context=user_context
    ).statement(
        project_id=project_id,
        contact_id=str(contact["id"]),
        page=page,
        page_size=page_size,
    )
    return _ok(request, "facility_booking.success.ledger_retrieved", data)


@handle_api_exceptions("get my reservation ledger")
@router.get(
    "/reservations/{reservation_id}/ledger",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=LEDGER_ENTRY_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_my_reservation_ledger(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List ledger entries for one of the resident's reservations."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).get_reservation(
        project_id=project_id,
        reservation_id=reservation_id,
        host_contact_id=str(contact["id"]),
    )
    items = await FacilityBookingLedgerService(
        db_connection=db_connection, user_context=user_context
    ).list_reservation(project_id=project_id, reservation_id=reservation_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="facility_booking.success.ledger_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get cancel quote")
@router.get(
    "/reservations/{reservation_id}/cancel-quote",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=CANCEL_QUOTE_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def get_cancel_quote(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Preview cancellation fee and refund."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).cancel_quote(
        project_id=project_id,
        reservation_id=reservation_id,
        host_contact_id=str(contact["id"]),
    )
    return _ok(request, "facility_booking.success.cancel_quote_retrieved", data)


@handle_api_exceptions("cancel resident reservation")
@router.post(
    "/reservations/{reservation_id}/cancel",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
async def cancel_reservation(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    body: CancelReservationRequest = Body(default=CancelReservationRequest()),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Cancel one of the resident's reservations."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).cancel(
        project_id=project_id,
        reservation_id=reservation_id,
        body=body,
        actor_type=FacilityReservationActorType.RESIDENT.value,
        host_contact_id=str(contact["id"]),
    )
    return _ok(request, "facility_booking.success.reservation_cancelled", data)


@handle_api_exceptions("reschedule resident reservation")
@router.post(
    "/reservations/{reservation_id}/reschedule",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
async def reschedule_reservation(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    body: RescheduleReservationRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Reschedule one of the resident's reservations."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).reschedule(
        project_id=project_id,
        reservation_id=reservation_id,
        body=body,
        actor_type=FacilityReservationActorType.RESIDENT.value,
        host_contact_id=str(contact["id"]),
    )
    return _ok(request, "facility_booking.success.reservation_rescheduled", data)


@handle_api_exceptions("get my booking invoices")
@router.get(
    "/invoices",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=INVOICE_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_my_invoices(
    request: Request,
    project_id: str = Path(...),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List the resident's facility booking invoices."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    items, total = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).list_invoices(
        project_id=project_id,
        contact_id=str(contact["id"]),
        status=status,
        page=page,
        page_size=page_size,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="facility_booking.success.invoices_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get my booking invoice")
@router.get(
    "/invoices/{invoice_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=INVOICE_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_my_invoice(
    request: Request,
    project_id: str = Path(...),
    invoice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one of the resident's invoices."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).get_invoice(project_id=project_id, invoice_id=invoice_id, contact_id=str(contact["id"]))
    return _ok(request, "facility_booking.success.invoice_retrieved", data)


@handle_api_exceptions("pay my booking invoice")
@router.post(
    "/invoices/{invoice_id}/pay",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=INVOICE_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
async def pay_my_invoice(
    request: Request,
    project_id: str = Path(...),
    invoice_id: str = Path(...),
    body: PayInvoiceRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Pay an invoice from the resident wallet or online."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).pay_invoice(
        project_id=project_id,
        invoice_id=invoice_id,
        body=body,
        allowed_methods={
            FacilityBookingPaymentMethod.WALLET.value,
            FacilityBookingPaymentMethod.ONLINE.value,
        },
        contact_id=str(contact["id"]),
    )
    return _ok(request, "facility_booking.success.invoice_paid", data)


@handle_api_exceptions("get my booking wallet")
@router.get(
    "/wallet",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=WALLET_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_my_wallet(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the resident's booking wallet."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).get_wallet(project_id=project_id, contact_id=str(contact["id"]))
    return _ok(request, "facility_booking.success.wallet_retrieved", data)


@handle_api_exceptions("top up my booking wallet")
@router.post(
    "/wallet/topup",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=WALLET_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
async def top_up_my_wallet(
    request: Request,
    project_id: str = Path(...),
    body: ResidentWalletTopUpRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Top up the resident's booking wallet."""
    user_context, contact = await ensure_resident_booking_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        request=request,
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).top_up(
        project_id=project_id,
        body=WalletTopUpRequest(
            contact_id=str(contact["id"]),
            amount=body.amount,
            method=body.method,
        ),
    )
    return _ok(request, "facility_booking.success.wallet_topped_up", data)
