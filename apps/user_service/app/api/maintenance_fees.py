"""Resident maintenance fee invoice APIs."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.fee_configuration import (
    PayMaintenanceFeeInvoiceRequest,
)
from apps.user_service.app.services.fee_calculation_service import (
    convert_major_to_minor,
)
from apps.user_service.app.services.fee_invoice_service import FeeInvoiceService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/maintenance-fees", tags=["Maintenance Fees"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("list resident maintenance fee invoices")
@router.get(
    "/invoices",
    status_code=http_status.HTTP_200_OK,
    summary="List my maintenance fee invoices",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_my_fee_invoices(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List maintenance fee invoices for units owned by the resident."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    contact_units_repo = ContactUnitsRepository(db_connection)
    links = await contact_units_repo.list_by_contact(
        organization_id=user_context.organization_id,
        contact_id=str(contact["id"]),
    )
    contact_unit_ids = [str(link["id"]) for link in links if link.get("status") == "active"]
    service = FeeInvoiceService(db_connection=db_connection, user_context=user_context)
    payload = await service.list_resident_invoices(
        contact_unit_ids=contact_unit_ids,
        page=page,
        page_size=page_size,
    )
    return list_response(
        request=request,
        items=payload["items"],
        total=payload["total"],
        page=page,
        page_size=page_size,
        message_key="fee_invoices.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("pay maintenance fee invoice")
@router.post(
    "/invoices/{invoice_id}/pay",
    status_code=http_status.HTTP_200_OK,
    summary="Pay a maintenance fee invoice",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="maintenance_fee_invoices",
    category="FINANCE",
)
async def pay_fee_invoice(
    request: Request,
    invoice_id: str = Path(...),
    body: PayMaintenanceFeeInvoiceRequest | None = Body(default=None),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Record payment for a resident-owned invoice (PSP integration follow-up)."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = FeeInvoiceService(db_connection=db_connection, user_context=user_context)
    invoice = await service.get_invoice(invoice_id=invoice_id)
    contact_units_repo = ContactUnitsRepository(db_connection)
    links = await contact_units_repo.list_by_contact(
        organization_id=user_context.organization_id,
        contact_id=str(contact["id"]),
    )
    owned_ids = {str(link["id"]) for link in links if link.get("status") == "active"}
    if invoice.get("contact_unit_id") not in owned_ids:
        from libs.shared_utils.http_exceptions import NotFoundException

        raise NotFoundException(
            message_key="fee_invoices.errors.invoice_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )
    amount_minor = convert_major_to_minor(body.amount) if body and body.amount is not None else None
    data = await service.record_payment(
        invoice_id=invoice_id,
        amount_minor=amount_minor,
        actor_user_id=user_context.user_id,
    )
    set_audit_context(
        request,
        user_context,
        table="maintenance_fee_invoices",
        requested_id=invoice_id,
        description=f"Recorded maintenance fee payment: {invoice_id}",
        risk_level="high",
        new_data=data,
    )
    return success_response(
        request=request,
        data=data,
        message_key="fee_invoices.success.payment_recorded",
        custom_code=CustomStatusCode.SUCCESS,
    )
