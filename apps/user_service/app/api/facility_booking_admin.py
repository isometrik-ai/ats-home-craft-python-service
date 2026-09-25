"""Staff facility booking API (project-scoped)."""

from __future__ import annotations

from datetime import date

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.facility_booking import (
    BookableFacilityListApiResponse,
    BookingInvoiceApiResponse,
    BookingInvoiceListApiResponse,
    BookingUnitApiResponse,
    CancelReservationRequest,
    ClosureApiResponse,
    CreateStaffReservationRequest,
    DayAvailabilityApiResponse,
    DeletedResourceApiResponse,
    DraftEvaluationApiResponse,
    FacilityBookingWorkspaceApiResponse,
    FacilityReservationApiResponse,
    FacilityReservationListApiResponse,
    GenerateInvoicesApiResponse,
    GenerateInvoicesRequest,
    LedgerEntryApiResponse,
    LedgerEntryListApiResponse,
    LedgerStatementApiResponse,
    MaintenanceWindowApiResponse,
    MonthOverviewListApiResponse,
    NextAvailabilityApiResponse,
    PayInvoiceRequest,
    PaymentOverviewApiResponse,
    ProjectBookingSettingsApiResponse,
    RaiseChargeRequest,
    RejectReservationRequest,
    RescheduleReservationRequest,
    ReservationNoteRequest,
    RoomAvailabilityListApiResponse,
    SchedulePeriodApiResponse,
    SlotBlockApiResponse,
    StaffAssignmentApiResponse,
    StaffAssignmentListApiResponse,
    StaffReservationDraftRequest,
    StaffReservationListQuery,
    UnbilledLedgerApiResponse,
    WalletAdjustRequest,
    WalletApiResponse,
    WalletLimitRequest,
    WalletTopUpRequest,
)
from apps.user_service.app.schemas.facility_booking_config import (
    UpdateFacilityBookingConfigRequest,
)
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
from apps.user_service.app.services.facility_booking_notification_service import (
    FacilityBookingNotificationService,
)
from apps.user_service.app.services.facility_reservation_service import (
    FacilityReservationService,
)
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from apps.user_service.app.utils.facility_booking_access import (
    ensure_booking_staff,
    ensure_can_operate_facility,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    FACILITY_BOOKING_MANAGEMENT_APPROVE,
    FACILITY_BOOKING_MANAGEMENT_BILLING,
    FACILITY_BOOKING_MANAGEMENT_CONFIGURE,
    FACILITY_BOOKING_MANAGEMENT_OPERATE,
    FACILITY_BOOKING_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects/{project_id}/facility-bookings", tags=["Facility Booking"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    409: {"description": "Conflict (stale version or overlapping inventory)."},
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
    "Bookable facilities for staff configuration.",
)
WORKSPACE_SUCCESS_RESPONSES = _ok_response(
    FacilityBookingWorkspaceApiResponse,
    "Booking config and inventory for one facility.",
)
BOOKING_UNIT_CREATED_RESPONSES = _created_response(
    BookingUnitApiResponse,
    "Bookable unit created.",
)
BOOKING_UNIT_SUCCESS_RESPONSES = _ok_response(
    BookingUnitApiResponse,
    "Bookable unit updated.",
)
SCHEDULE_CREATED_RESPONSES = _created_response(
    SchedulePeriodApiResponse,
    "Schedule period created.",
)
SCHEDULE_SUCCESS_RESPONSES = _ok_response(
    SchedulePeriodApiResponse,
    "Schedule period updated.",
)
SLOT_BLOCK_CREATED_RESPONSES = _created_response(
    SlotBlockApiResponse,
    "Slot block created.",
)
CLOSURE_CREATED_RESPONSES = _created_response(
    ClosureApiResponse,
    "Closure day created.",
)
MAINTENANCE_CREATED_RESPONSES = _created_response(
    MaintenanceWindowApiResponse,
    "Maintenance window created.",
)
DELETED_RESOURCE_SUCCESS_RESPONSES = _ok_response(
    DeletedResourceApiResponse,
    "Resource deleted; returns the removed id.",
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
    "Paginated reservation list.",
)
STAFF_ASSIGNMENT_LIST_SUCCESS_RESPONSES = _ok_response(
    StaffAssignmentListApiResponse,
    "Staff members assigned to operate facilities.",
)
STAFF_ASSIGNMENT_SUCCESS_RESPONSES = _ok_response(
    StaffAssignmentApiResponse,
    "Staff facility assignment saved.",
)
SETTINGS_SUCCESS_RESPONSES = _ok_response(
    ProjectBookingSettingsApiResponse,
    "Project-wide facility booking settings.",
)
LEDGER_ENTRY_LIST_SUCCESS_RESPONSES = _ok_response(
    LedgerEntryListApiResponse,
    "Paginated ledger entries.",
)
LEDGER_STATEMENT_SUCCESS_RESPONSES = _ok_response(
    LedgerStatementApiResponse,
    "Contact ledger statement with balance.",
)
UNBILLED_LEDGER_SUCCESS_RESPONSES = _ok_response(
    UnbilledLedgerApiResponse,
    "Unbilled ledger totals grouped by contact.",
)
LEDGER_ENTRY_CREATED_RESPONSES = _created_response(
    LedgerEntryApiResponse,
    "Manual charge posted to contact ledger.",
)
PAYMENTS_OVERVIEW_SUCCESS_RESPONSES = _ok_response(
    PaymentOverviewApiResponse,
    "Billing overview metrics for the project.",
)
INVOICE_LIST_SUCCESS_RESPONSES = _ok_response(
    BookingInvoiceListApiResponse,
    "Paginated facility booking invoices.",
)
GENERATE_INVOICES_CREATED_RESPONSES = _created_response(
    GenerateInvoicesApiResponse,
    "Invoices generated from unbilled ledger entries.",
)
INVOICE_SUCCESS_RESPONSES = _ok_response(
    BookingInvoiceApiResponse,
    "Facility booking invoice detail or payment result.",
)
WALLET_SUCCESS_RESPONSES = _ok_response(
    WalletApiResponse,
    "Contact booking wallet with recent transactions.",
)


async def _staff(request, current_user, db_connection, project_id, permission):
    """Resolve staff user context with facility-booking permissions."""
    return await ensure_booking_staff(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=permission,
        request=request,
    )


def _ok(request, message_key, data, *, created=False):
    """Wrap a success or created response for facility booking admin routes."""
    return success_response(
        request=request,
        message_key=message_key,
        custom_code=CustomStatusCode.CREATED if created else CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_201_CREATED if created else http_status.HTTP_200_OK,
        data=data,
    )


@handle_api_exceptions("list bookable facilities")
@router.get(
    "/facilities",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=FACILITIES_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_bookable_facilities(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List bookable facilities for staff configuration."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    items = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).list_bookable_facilities(project_id=project_id)
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


@handle_api_exceptions("get facility booking workspace")
@router.get(
    "/facilities/{facility_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=WORKSPACE_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_facility_workspace(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return booking config and inventory for one facility."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).get_workspace(project_id=project_id, facility_id=facility_id)
    return _ok(request, "facility_booking.success.config_retrieved", data)


@handle_api_exceptions("update facility booking config")
@router.patch(
    "/facilities/{facility_id}/config",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=WORKSPACE_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="facility_booking_configs",
    category="FACILITY_BOOKING",
)
async def update_facility_config(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    body: UpdateFacilityBookingConfigRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Patch facility booking rules with optimistic concurrency."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).update_config(project_id=project_id, facility_id=facility_id, body=body)
    set_audit_context(
        request,
        user_context,
        project_id=project_id,
        table="facility_booking_configs",
        requested_id=facility_id,
        description=f"Updated booking config for facility {facility_id}",
        new_data=data.get("config"),
    )
    return _ok(request, "facility_booking.success.config_updated", data)


@handle_api_exceptions("create booking unit")
@router.post(
    "/facilities/{facility_id}/units",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=BOOKING_UNIT_CREATED_RESPONSES,
)
@limiter.limit("40/minute")
async def create_booking_unit(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    body: CreateBookingUnitRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Add a bookable unit (court, room, tee sheet row)."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).create_unit(project_id=project_id, facility_id=facility_id, body=body)
    return _ok(request, "facility_booking.success.unit_created", data, created=True)


@handle_api_exceptions("update booking unit")
@router.patch(
    "/facilities/{facility_id}/units/{unit_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=BOOKING_UNIT_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def update_booking_unit(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    unit_id: str = Path(...),
    body: UpdateBookingUnitRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update a bookable unit."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).update_unit(project_id=project_id, facility_id=facility_id, unit_id=unit_id, body=body)
    return _ok(request, "facility_booking.success.unit_updated", data)


@handle_api_exceptions("delete booking unit")
@router.delete(
    "/facilities/{facility_id}/units/{unit_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=DELETED_RESOURCE_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def delete_booking_unit(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    unit_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Remove a bookable unit."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).delete_inventory(
        project_id=project_id,
        facility_id=facility_id,
        table="facility_booking_units",
        row_id=unit_id,
    )
    return _ok(request, "facility_booking.success.unit_deleted", {"id": unit_id})


@handle_api_exceptions("create schedule period")
@router.post(
    "/facilities/{facility_id}/schedules",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=SCHEDULE_CREATED_RESPONSES,
)
@limiter.limit("40/minute")
async def create_schedule(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    body: CreateSchedulePeriodRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create a seasonal schedule period for a facility."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).create_schedule(project_id=project_id, facility_id=facility_id, body=body)
    return _ok(request, "facility_booking.success.schedule_created", data, created=True)


@handle_api_exceptions("update schedule period")
@router.patch(
    "/facilities/{facility_id}/schedules/{period_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=SCHEDULE_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def update_schedule(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    period_id: str = Path(...),
    body: UpdateSchedulePeriodRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update a schedule period."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).update_schedule(
        project_id=project_id, facility_id=facility_id, period_id=period_id, body=body
    )
    return _ok(request, "facility_booking.success.schedule_updated", data)


@handle_api_exceptions("delete schedule period")
@router.delete(
    "/facilities/{facility_id}/schedules/{period_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=DELETED_RESOURCE_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def delete_schedule(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    period_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete a schedule period."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).delete_inventory(
        project_id=project_id,
        facility_id=facility_id,
        table="facility_schedule_periods",
        row_id=period_id,
    )
    return _ok(request, "facility_booking.success.schedule_deleted", {"id": period_id})


@handle_api_exceptions("create slot block")
@router.post(
    "/facilities/{facility_id}/slot-blocks",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=SLOT_BLOCK_CREATED_RESPONSES,
)
@limiter.limit("40/minute")
async def create_slot_block(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    body: CreateSlotBlockRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Block slots on the facility calendar."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).create_slot_block(project_id=project_id, facility_id=facility_id, body=body)
    return _ok(request, "facility_booking.success.block_created", data, created=True)


@handle_api_exceptions("delete slot block")
@router.delete(
    "/facilities/{facility_id}/slot-blocks/{block_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=DELETED_RESOURCE_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def delete_slot_block(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    block_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Remove a slot block."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).delete_inventory(
        project_id=project_id,
        facility_id=facility_id,
        table="facility_slot_blocks",
        row_id=block_id,
    )
    return _ok(request, "facility_booking.success.block_deleted", {"id": block_id})


@handle_api_exceptions("create closure")
@router.post(
    "/facilities/{facility_id}/closures",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=CLOSURE_CREATED_RESPONSES,
)
@limiter.limit("40/minute")
async def create_closure(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    body: CreateClosureRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Mark the facility closed on a date."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).create_closure(project_id=project_id, facility_id=facility_id, body=body)
    return _ok(request, "facility_booking.success.closure_created", data, created=True)


@handle_api_exceptions("delete closure")
@router.delete(
    "/facilities/{facility_id}/closures/{closure_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=DELETED_RESOURCE_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def delete_closure(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    closure_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Remove a closure day."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).delete_inventory(
        project_id=project_id,
        facility_id=facility_id,
        table="facility_closures",
        row_id=closure_id,
    )
    return _ok(request, "facility_booking.success.closure_deleted", {"id": closure_id})


@handle_api_exceptions("create maintenance window")
@router.post(
    "/facilities/{facility_id}/maintenance",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=MAINTENANCE_CREATED_RESPONSES,
)
@limiter.limit("40/minute")
async def create_maintenance(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    body: CreateMaintenanceWindowRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Add a maintenance window on a date."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).create_maintenance(project_id=project_id, facility_id=facility_id, body=body)
    return _ok(request, "facility_booking.success.maintenance_created", data, created=True)


@handle_api_exceptions("delete maintenance window")
@router.delete(
    "/facilities/{facility_id}/maintenance/{window_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=DELETED_RESOURCE_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def delete_maintenance(
    request: Request,
    project_id: str = Path(...),
    facility_id: str = Path(...),
    window_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Remove a maintenance window."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).delete_inventory(
        project_id=project_id,
        facility_id=facility_id,
        table="facility_maintenance_windows",
        row_id=window_id,
    )
    return _ok(request, "facility_booking.success.maintenance_deleted", {"id": window_id})


@handle_api_exceptions("get day availability")
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
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    data = await FacilityAvailabilityService(
        db_connection=db_connection, user_context=user_context
    ).availability_day(project_id=project_id, facility_id=facility_id, local_date=local_date)
    return _ok(request, "facility_booking.success.availability_retrieved", data)


@handle_api_exceptions("get month availability")
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
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    service = FacilityAvailabilityService(db_connection=db_connection, user_context=user_context)
    if summary:
        data = await service.availability_month_summary(
            project_id=project_id, facility_id=facility_id, start=start
        )
    else:
        data = await service.availability_month(
            project_id=project_id, facility_id=facility_id, start=start
        )
    return _ok(request, "facility_booking.success.availability_retrieved", data)


@handle_api_exceptions("get next availability")
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
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    data = await FacilityAvailabilityService(
        db_connection=db_connection, user_context=user_context
    ).next_availability(project_id=project_id, facility_id=facility_id)
    return _ok(request, "facility_booking.success.availability_retrieved", data)


@handle_api_exceptions("get room availability")
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
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    data = await FacilityAvailabilityService(
        db_connection=db_connection, user_context=user_context
    ).room_availability(
        project_id=project_id, facility_id=facility_id, check_in=check_in, check_out=check_out
    )
    return _ok(request, "facility_booking.success.availability_retrieved", data)


@handle_api_exceptions("quote staff reservation")
@router.post(
    "/reservations/quote",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=DRAFT_EVALUATION_SUCCESS_RESPONSES,
)
@limiter.limit("60/minute")
async def quote_staff_reservation(
    request: Request,
    project_id: str = Path(...),
    body: StaffReservationDraftRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Validate a draft and return a staff price quote."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_OPERATE
    )
    data = await FacilityAvailabilityService(
        db_connection=db_connection, user_context=user_context
    ).evaluate_draft(
        project_id=project_id,
        body=body,
        host_contact_id=body.host_contact_id,
        staff_override=True,
    )
    return _ok(request, "facility_booking.success.quote_retrieved", data)


@handle_api_exceptions("create staff reservation")
@router.post(
    "/reservations",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=RESERVATION_CREATED_RESPONSES,
)
@limiter.limit("40/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="facility_reservations",
    category="FACILITY_BOOKING",
)
async def create_staff_reservation(
    request: Request,
    project_id: str = Path(...),
    body: CreateStaffReservationRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create a reservation on behalf of a resident."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_OPERATE
    )
    await ensure_can_operate_facility(
        current_user=current_user,
        db_connection=db_connection,
        user_context=user_context,
        project_id=project_id,
        facility_id=body.facility_id,
        request=request,
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).create_staff(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        project_id=project_id,
        table="facility_reservations",
        requested_id=str(data.get("id", "")),
        description="Staff created a facility reservation",
        new_data=data,
    )
    return _ok(request, "facility_booking.success.reservation_created", data, created=True)


@handle_api_exceptions("list facility reservations")
@router.get(
    "/reservations",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_reservations(
    request: Request,
    project_id: str = Path(...),
    query: StaffReservationListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List facility reservations with filters."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    items, total = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).list_reservations(
        project_id=project_id,
        facility_id=query.facility_id,
        statuses=[query.status.value] if query.status else None,
        contact_id=query.contact_id,
        date_from=query.date_from,
        date_to=query.date_to,
        search=query.search,
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


@handle_api_exceptions("list pending facility reservations")
@router.get(
    "/reservations/pending",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_pending_reservations(
    request: Request,
    project_id: str = Path(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List reservations awaiting staff approval."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_APPROVE
    )
    items, total = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).list_reservations(
        project_id=project_id,
        statuses=["pending_approval"],
        page=page,
        page_size=page_size,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="facility_booking.success.reservations_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get facility reservation")
@router.get(
    "/reservations/{reservation_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_reservation(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return reservation detail with optional timeline events."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).get_reservation(project_id=project_id, reservation_id=reservation_id, include_events=True)
    return _ok(request, "facility_booking.success.reservation_retrieved", data)


async def _operate_transition(request, project_id, reservation_id, current_user, db_connection, fn):
    """Run a reservation lifecycle action with facility access checks."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_OPERATE
    )
    service = FacilityReservationService(db_connection=db_connection, user_context=user_context)
    existing = await service.get_reservation(project_id=project_id, reservation_id=reservation_id)
    await ensure_can_operate_facility(
        current_user=current_user,
        db_connection=db_connection,
        user_context=user_context,
        project_id=project_id,
        facility_id=existing["facility_id"],
        request=request,
    )
    return await fn(service)


@handle_api_exceptions("approve facility reservation")
@router.post(
    "/reservations/{reservation_id}/approve",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def approve_reservation(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Approve a pending reservation."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_APPROVE
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).approve(project_id=project_id, reservation_id=reservation_id)
    return _ok(request, "facility_booking.success.reservation_approved", data)


@handle_api_exceptions("reject facility reservation")
@router.post(
    "/reservations/{reservation_id}/reject",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def reject_reservation(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    body: RejectReservationRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Reject a pending reservation."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_APPROVE
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).reject(project_id=project_id, reservation_id=reservation_id, body=body)
    return _ok(request, "facility_booking.success.reservation_rejected", data)


@handle_api_exceptions("check in facility reservation")
@router.post(
    "/reservations/{reservation_id}/check-in",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def check_in_reservation(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Check in a confirmed reservation."""
    data = await _operate_transition(
        request,
        project_id,
        reservation_id,
        current_user,
        db_connection,
        lambda svc: svc.check_in(project_id=project_id, reservation_id=reservation_id),
    )
    return _ok(request, "facility_booking.success.reservation_checked_in", data)


@handle_api_exceptions("complete facility reservation")
@router.post(
    "/reservations/{reservation_id}/complete",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def complete_reservation(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Mark a checked-in reservation completed."""
    data = await _operate_transition(
        request,
        project_id,
        reservation_id,
        current_user,
        db_connection,
        lambda svc: svc.complete(project_id=project_id, reservation_id=reservation_id),
    )
    return _ok(request, "facility_booking.success.reservation_completed", data)


@handle_api_exceptions("mark facility reservation no-show")
@router.post(
    "/reservations/{reservation_id}/no-show",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def no_show_reservation(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Mark a reservation as no-show."""
    data = await _operate_transition(
        request,
        project_id,
        reservation_id,
        current_user,
        db_connection,
        lambda svc: svc.mark_no_show(project_id=project_id, reservation_id=reservation_id),
    )
    return _ok(request, "facility_booking.success.reservation_no_show", data)


@handle_api_exceptions("admin cancel facility reservation")
@router.post(
    "/reservations/{reservation_id}/admin-cancel",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def admin_cancel_reservation(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    body: CancelReservationRequest = Body(default=CancelReservationRequest()),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Cancel a reservation as staff (full refund)."""
    from apps.user_service.app.schemas.enums import FacilityReservationActorType

    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).cancel(
        project_id=project_id,
        reservation_id=reservation_id,
        body=body,
        actor_type=FacilityReservationActorType.STAFF.value,
    )
    return _ok(request, "facility_booking.success.reservation_cancelled", data)


@handle_api_exceptions("admin reschedule facility reservation")
@router.post(
    "/reservations/{reservation_id}/admin-reschedule",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def admin_reschedule_reservation(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    body: RescheduleReservationRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Reschedule a reservation as staff."""
    from apps.user_service.app.schemas.enums import FacilityReservationActorType

    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).reschedule(
        project_id=project_id,
        reservation_id=reservation_id,
        body=body,
        actor_type=FacilityReservationActorType.STAFF.value,
        staff_override=True,
    )
    return _ok(request, "facility_booking.success.reservation_rescheduled", data)


@handle_api_exceptions("add reservation note")
@router.post(
    "/reservations/{reservation_id}/notes",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=RESERVATION_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def add_reservation_note(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    body: ReservationNoteRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Append an internal note to a reservation."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_OPERATE
    )
    data = await FacilityReservationService(
        db_connection=db_connection, user_context=user_context
    ).add_note(project_id=project_id, reservation_id=reservation_id, body=body)
    return _ok(request, "facility_booking.success.note_added", data)


@handle_api_exceptions("list staff assignments")
@router.get(
    "/staff-assignments",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=STAFF_ASSIGNMENT_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_staff_assignments(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List staff members assigned to operate facilities."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    items = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).list_staff_assignments(project_id=project_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="facility_booking.success.assignments_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("upsert staff assignment")
@router.put(
    "/staff-assignments",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=STAFF_ASSIGNMENT_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def upsert_staff_assignment(
    request: Request,
    project_id: str = Path(...),
    body: UpsertStaffAssignmentRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Assign or replace facility operate permissions for staff."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).upsert_staff_assignment(project_id=project_id, body=body)
    return _ok(request, "facility_booking.success.assignment_saved", data)


@handle_api_exceptions("delete staff assignment")
@router.delete(
    "/staff-assignments/{assignment_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=DELETED_RESOURCE_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def delete_staff_assignment(
    request: Request,
    project_id: str = Path(...),
    assignment_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Remove a staff facility assignment."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).delete_staff_assignment(project_id=project_id, assignment_id=assignment_id)
    return _ok(request, "facility_booking.success.assignment_deleted", {"id": assignment_id})


@handle_api_exceptions("get booking settings")
@router.get(
    "/settings",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=SETTINGS_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_settings(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return project-wide facility booking settings."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).get_settings(project_id=project_id)
    return _ok(request, "facility_booking.success.settings_retrieved", data)


@handle_api_exceptions("update booking settings")
@router.put(
    "/settings",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=SETTINGS_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def update_settings(
    request: Request,
    project_id: str = Path(...),
    body: UpdateProjectBookingSettingsRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update project-wide facility booking settings."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_CONFIGURE
    )
    data = await FacilityBookingConfigService(
        db_connection=db_connection, user_context=user_context
    ).update_settings(project_id=project_id, body=body)
    return _ok(request, "facility_booking.success.settings_updated", data)


@handle_api_exceptions("list facility booking ledger")
@router.get(
    "/ledger",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=LEDGER_ENTRY_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_ledger(
    request: Request,
    project_id: str = Path(...),
    contact_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List ledger entries across contacts."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    items, total = await FacilityBookingLedgerService(
        db_connection=db_connection, user_context=user_context
    ).list_project(project_id=project_id, contact_id=contact_id, page=page, page_size=page_size)
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="facility_booking.success.ledger_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("list unbilled facility booking charges")
@router.get(
    "/ledger/unbilled",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=UNBILLED_LEDGER_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_unbilled(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List unbilled ledger totals by contact."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_BILLING
    )
    data = await FacilityBookingLedgerService(
        db_connection=db_connection, user_context=user_context
    ).unbilled(project_id=project_id)
    return _ok(request, "facility_booking.success.unbilled_retrieved", data)


@handle_api_exceptions("get contact facility booking ledger")
@router.get(
    "/contacts/{contact_id}/ledger",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=LEDGER_STATEMENT_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_contact_ledger(
    request: Request,
    project_id: str = Path(...),
    contact_id: str = Path(...),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return a contact ledger statement."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    data = await FacilityBookingLedgerService(
        db_connection=db_connection, user_context=user_context
    ).statement(project_id=project_id, contact_id=contact_id, page=page, page_size=page_size)
    return _ok(request, "facility_booking.success.ledger_retrieved", data)


@handle_api_exceptions("get reservation ledger")
@router.get(
    "/reservations/{reservation_id}/ledger",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=LEDGER_ENTRY_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_reservation_ledger(
    request: Request,
    project_id: str = Path(...),
    reservation_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List ledger entries for one reservation."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
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


@handle_api_exceptions("raise facility booking charge")
@router.post(
    "/charges",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=LEDGER_ENTRY_CREATED_RESPONSES,
)
@limiter.limit("40/minute")
async def raise_charge(
    request: Request,
    project_id: str = Path(...),
    body: RaiseChargeRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Post a manual charge to a contact ledger."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_BILLING
    )
    service = FacilityBookingLedgerService(db_connection=db_connection, user_context=user_context)
    data = await service.raise_charge(project_id=project_id, body=body)
    await FacilityBookingNotificationService(
        db_connection=db_connection, organization_id=user_context.organization_id
    ).charge_posted(
        contact_id=body.contact_id,
        reservation_id=body.reservation_id,
        amount=int(data["amount"]),
        description=body.description.strip(),
    )
    return _ok(request, "facility_booking.success.charge_posted", data, created=True)


@handle_api_exceptions("get facility booking payments overview")
@router.get(
    "/payments/overview",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=PAYMENTS_OVERVIEW_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def payments_overview(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return billing overview metrics for the project."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_BILLING
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).overview(project_id=project_id)
    return _ok(request, "facility_booking.success.payments_overview_retrieved", data)


@handle_api_exceptions("list facility booking invoices")
@router.get(
    "/invoices",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=INVOICE_LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_invoices(
    request: Request,
    project_id: str = Path(...),
    contact_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List facility booking invoices."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    items, total = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).list_invoices(
        project_id=project_id,
        contact_id=contact_id,
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


@handle_api_exceptions("generate facility booking invoices")
@router.post(
    "/invoices/generate",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=GENERATE_INVOICES_CREATED_RESPONSES,
)
@limiter.limit("20/minute")
async def generate_invoices(
    request: Request,
    project_id: str = Path(...),
    body: GenerateInvoicesRequest = Body(default=GenerateInvoicesRequest()),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Generate invoices from unbilled ledger entries."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_BILLING
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).generate_invoices(project_id=project_id, body=body)
    return _ok(request, "facility_booking.success.invoices_generated", data, created=True)


@handle_api_exceptions("get facility booking invoice")
@router.get(
    "/invoices/{invoice_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=INVOICE_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_invoice(
    request: Request,
    project_id: str = Path(...),
    invoice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one facility booking invoice."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).get_invoice(project_id=project_id, invoice_id=invoice_id)
    return _ok(request, "facility_booking.success.invoice_retrieved", data)


@handle_api_exceptions("pay facility booking invoice")
@router.post(
    "/invoices/{invoice_id}/pay",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=INVOICE_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def pay_invoice(
    request: Request,
    project_id: str = Path(...),
    invoice_id: str = Path(...),
    body: PayInvoiceRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Record payment for an invoice."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_BILLING
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).pay_invoice(project_id=project_id, invoice_id=invoice_id, body=body)
    return _ok(request, "facility_booking.success.invoice_paid", data)


@handle_api_exceptions("get facility booking wallet")
@router.get(
    "/wallets/{contact_id}",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=WALLET_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_wallet(
    request: Request,
    project_id: str = Path(...),
    contact_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return a contact booking wallet with recent transactions."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_VIEW
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).get_wallet(project_id=project_id, contact_id=contact_id)
    return _ok(request, "facility_booking.success.wallet_retrieved", data)


@handle_api_exceptions("top up facility booking wallet")
@router.post(
    "/wallets/topup",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=WALLET_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def top_up_wallet(
    request: Request,
    project_id: str = Path(...),
    body: WalletTopUpRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Top up a contact booking wallet."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_BILLING
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).top_up(project_id=project_id, body=body)
    return _ok(request, "facility_booking.success.wallet_topped_up", data)


@handle_api_exceptions("adjust facility booking wallet")
@router.post(
    "/wallets/adjust",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=WALLET_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def adjust_wallet(
    request: Request,
    project_id: str = Path(...),
    body: WalletAdjustRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Apply a manual wallet adjustment."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_BILLING
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).adjust(project_id=project_id, body=body)
    return _ok(request, "facility_booking.success.wallet_adjusted", data)


@handle_api_exceptions("set facility booking wallet limit")
@router.put(
    "/wallets/{contact_id}/limit",
    status_code=http_status.HTTP_200_OK,
    response_model=None,
    responses=WALLET_SUCCESS_RESPONSES,
)
@limiter.limit("40/minute")
async def set_wallet_limit(
    request: Request,
    project_id: str = Path(...),
    contact_id: str = Path(...),
    body: WalletLimitRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Set a custom credit limit on a contact wallet."""
    user_context = await _staff(
        request, current_user, db_connection, project_id, FACILITY_BOOKING_MANAGEMENT_BILLING
    )
    data = await FacilityBookingBillingService(
        db_connection=db_connection, user_context=user_context
    ).set_limit(project_id=project_id, contact_id=contact_id, body=body)
    return _ok(request, "facility_booking.success.wallet_limit_updated", data)
