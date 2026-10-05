"""Staff and resident endpoints for fee invoices, payments, and the unit balance."""

from __future__ import annotations

from datetime import date

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.fee_billing import (
    FeeInvoiceDetailApiResponse,
    FeeInvoiceListApiResponse,
    FeeInvoiceStatus,
    FeeOutstandingSummaryApiResponse,
    FeePaymentApiResponse,
    FeeUnitBalanceApiResponse,
    RecordFeePaymentRequest,
)
from apps.user_service.app.services.fee_billing_service import FeeBillingService
from apps.user_service.app.services.fee_invoice_mail import (
    collect_fee_payment_messages,
    dispatch_fee_invoice_emails,
)
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    UserContext,
    ensure_staff_project_access,
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    FINANCE_MANAGEMENT_EDIT,
    FINANCE_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import ForbiddenException, NotFoundException
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects/{project_id}", tags=["Fee invoices"])
unit_router = APIRouter(prefix="/units/{unit_id}", tags=["Fee invoices"])


async def _resident_for_unit(
    *,
    current_user: dict,
    db_connection: asyncpg.Connection,
    unit_id: str,
    request: Request,
) -> tuple[UserContext, str]:
    """Confirm the caller lives in the unit and return its project id."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user,
        db_connection,
        request=request,
    )
    repository = ContactUnitsRepository(db_connection)
    organization_id = str(user_context.organization_id)
    unit = await repository.get_unit_project(
        organization_id=organization_id,
        unit_id=unit_id,
    )
    if unit is None or not unit.get("project_id"):
        raise NotFoundException(
            message_key="projects.errors.unit_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    linked = await repository.contact_has_active_unit(
        organization_id=organization_id,
        contact_id=str(contact["id"]),
        unit_id=unit_id,
    )
    if not linked:
        raise ForbiddenException(
            message_key="fee_billing.errors.unit_not_accessible",
            custom_code=CustomStatusCode.FORBIDDEN,
        )
    return user_context, str(unit["project_id"])


_ERRORS: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Invoice not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("list fee invoices")
@router.get(
    "/fee-invoices",
    status_code=http_status.HTTP_200_OK,
    summary="List fee invoices for a project",
    response_model=None,
    responses={
        **_ERRORS,
        200: {"model": FeeInvoiceListApiResponse, "description": "Invoices for the project."},
    },
)
@limiter.limit("100/minute")
async def list_fee_invoices(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    *,
    unit_id: str | None = Query(default=None, description="Limit the list to one unit."),
    status: FeeInvoiceStatus | None = Query(
        default=None,
        description="issued, partial, paid, or overdue.",
    ),
    months: list[date] | None = Query(
        default=None,
        description="Billing months to include. Any day in the month selects that month.",
    ),
    page: int = Query(default=1, ge=1, description="Page number."),
    page_size: int = Query(default=20, ge=1, le=100, description="Invoices per page."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return project invoices, filtered by unit, status, and billing month."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=FINANCE_MANAGEMENT_VIEW,
        request=request,
    )
    items, total = await FeeBillingService(db_connection).list_invoices(
        organization_id=str(user_context.organization_id),
        project_id=project_id,
        unit_id=unit_id,
        status=status.value if status is not None else None,
        billing_months=months,
        page=page,
        page_size=page_size,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="fee_billing.success.invoices_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("list resident fee invoices")
@unit_router.get(
    "/fee-invoices",
    status_code=http_status.HTTP_200_OK,
    summary="List fee invoices for a resident's unit",
    response_model=None,
    responses={
        **_ERRORS,
        200: {
            "model": FeeInvoiceListApiResponse,
            "description": "Invoices for the resident's unit.",
        },
    },
)
@limiter.limit("100/minute")
async def list_resident_fee_invoices(
    request: Request,
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    *,
    status: FeeInvoiceStatus | None = Query(
        default=None,
        description="issued, partial, paid, or overdue.",
    ),
    months: list[date] | None = Query(
        default=None,
        description="Billing months to include. Any day in the month selects that month.",
    ),
    page: int = Query(default=1, ge=1, description="Page number."),
    page_size: int = Query(default=20, ge=1, le=100, description="Invoices per page."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return invoices for one unit the resident lives in."""
    user_context, project_id = await _resident_for_unit(
        current_user=current_user,
        db_connection=db_connection,
        unit_id=unit_id,
        request=request,
    )
    items, total = await FeeBillingService(db_connection).list_invoices(
        organization_id=str(user_context.organization_id),
        project_id=project_id,
        unit_id=unit_id,
        status=status.value if status is not None else None,
        billing_months=months,
        page=page,
        page_size=page_size,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="fee_billing.success.invoices_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get resident fee outstanding")
@unit_router.get(
    "/fee-outstanding",
    status_code=http_status.HTTP_200_OK,
    summary="Pending payments total for a resident unit",
    response_model=None,
    responses={
        **_ERRORS,
        200: {
            "model": FeeOutstandingSummaryApiResponse,
            "description": "Total outstanding, overdue, unpaid bills, and late fee.",
        },
    },
)
@limiter.limit("100/minute")
async def get_resident_fee_outstanding(
    request: Request,
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the amount still to pay for a unit the caller lives in."""
    user_context, project_id = await _resident_for_unit(
        current_user=current_user,
        db_connection=db_connection,
        unit_id=unit_id,
        request=request,
    )
    summary = await FeeBillingService(db_connection).outstanding_summary(
        organization_id=str(user_context.organization_id),
        project_id=project_id,
        unit_id=unit_id,
    )
    return success_response(
        request=request,
        message_key="fee_billing.success.outstanding_retrieved",
        data=summary,
    )


@handle_api_exceptions("get resident fee invoice")
@unit_router.get(
    "/fee-invoices/{invoice_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get one fee invoice for a resident unit",
    response_model=None,
    responses={
        **_ERRORS,
        200: {
            "model": FeeInvoiceDetailApiResponse,
            "description": "Invoice lines, tax, round-off, and payments.",
        },
    },
)
@limiter.limit("100/minute")
async def get_resident_fee_invoice(
    request: Request,
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    invoice_id: str = Path(..., description="Invoice identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one invoice, its lines, and its payments for a unit the caller lives in."""
    user_context, project_id = await _resident_for_unit(
        current_user=current_user,
        db_connection=db_connection,
        unit_id=unit_id,
        request=request,
    )
    detail = await FeeBillingService(db_connection).invoice_detail(
        organization_id=str(user_context.organization_id),
        project_id=project_id,
        unit_id=unit_id,
        invoice_id=invoice_id,
    )
    return success_response(
        request=request,
        message_key="fee_billing.success.invoice_retrieved",
        data=detail,
    )


@handle_api_exceptions("get unit fee balance")
@router.get(
    "/units/{unit_id}/fee-balance",
    status_code=http_status.HTTP_200_OK,
    summary="Open fee balance for a unit",
    response_model=None,
    responses={
        **_ERRORS,
        200: {
            "model": FeeUnitBalanceApiResponse,
            "description": "Arrears, late fee, and current charges.",
        },
    },
)
@limiter.limit("100/minute")
async def get_unit_fee_balance(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    unit_id: str = Path(..., description="Unit identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return unpaid invoices split into arrears, late fee, and current charges."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=FINANCE_MANAGEMENT_VIEW,
        request=request,
    )
    data = await FeeBillingService(db_connection).unit_balance(
        organization_id=str(user_context.organization_id),
        project_id=project_id,
        unit_id=unit_id,
    )
    return success_response(
        request=request,
        message_key="fee_billing.success.balance_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("record fee payment")
@router.post(
    "/fee-invoices/{invoice_id}/payments",
    status_code=http_status.HTTP_201_CREATED,
    summary="Record a payment against a fee invoice",
    response_model=None,
    responses={
        **_ERRORS,
        201: {"model": FeePaymentApiResponse, "description": "Payment recorded."},
    },
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="fee_invoice_payments",
    category="FEE_BILLING",
)
async def record_fee_payment(
    request: Request,
    background_tasks: BackgroundTasks,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    invoice_id: str = Path(..., description="Fee invoice identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: RecordFeePaymentRequest = Body(...),
):
    """Record a payment. The invoice stays at its issued total."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=FINANCE_MANAGEMENT_EDIT,
        request=request,
    )
    data = await FeeBillingService(db_connection).record_payment(
        organization_id=str(user_context.organization_id),
        project_id=project_id,
        invoice_id=invoice_id,
        amount=body.amount,
        paid_on=body.paid_on,
        mode=body.mode.value,
        reference=body.reference,
    )
    messages = await collect_fee_payment_messages(
        db_connection,
        {
            "organization_id": str(user_context.organization_id),
            "project_id": project_id,
            "invoice_id": invoice_id,
            "amount": data["amount"],
            "paid_on": data["paid_on"],
            "mode": data["mode"],
            "reference": data["reference"],
            "outstanding": data["outstanding"],
        },
    )
    if messages:
        background_tasks.add_task(dispatch_fee_invoice_emails, messages)
    set_audit_context(
        request,
        user_context,
        table="fee_invoice_payments",
        description=f"Recorded payment on fee invoice {invoice_id}",
        requested_id=invoice_id,
        project_id=project_id,
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="fee_billing.success.payment_recorded",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )
