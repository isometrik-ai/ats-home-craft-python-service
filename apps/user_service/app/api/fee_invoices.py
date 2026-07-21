"""Maintenance fee invoice admin and scheduler APIs."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.services.fee_invoice_service import FeeInvoiceService
from apps.user_service.app.services.fee_scheduler_service import FeeSchedulerService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    FINANCE_MANAGEMENT_ADMIN,
    FINANCE_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Fee Invoices"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("list maintenance fee invoices")
@router.get(
    "/{project_id}/fee-invoices",
    status_code=http_status.HTTP_200_OK,
    summary="List maintenance fee invoices",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_fee_invoices(
    request: Request,
    project_id: str = Path(...),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List invoices for a project (Fee Management)."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=FINANCE_MANAGEMENT_VIEW,
    )
    service = FeeInvoiceService(db_connection=db_connection, user_context=user_context)
    payload = await service.list_project_invoices(
        project_id=project_id,
        status=status,
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


@handle_api_exceptions("get maintenance fee invoice")
@router.get(
    "/{project_id}/fee-invoices/{invoice_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get maintenance fee invoice",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_fee_invoice(
    request: Request,
    project_id: str = Path(...),
    invoice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Fetch a single maintenance fee invoice."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=FINANCE_MANAGEMENT_VIEW,
    )
    service = FeeInvoiceService(db_connection=db_connection, user_context=user_context)
    data = await service.get_invoice(invoice_id=invoice_id, project_id=project_id)
    return success_response(
        request=request,
        data=data,
        message_key="fee_invoices.success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("generate maintenance fee invoices")
@router.post(
    "/{project_id}/fee-invoices/generate",
    status_code=http_status.HTTP_200_OK,
    summary="Generate maintenance fee invoices",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("10/minute")
async def generate_fee_invoices(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Generate invoices for billable units in a project."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=FINANCE_MANAGEMENT_ADMIN,
    )
    service = FeeInvoiceService(db_connection=db_connection, user_context=user_context)
    data = await service.generate_invoices_for_project(project_id=project_id)
    return success_response(
        request=request,
        data=data,
        message_key="fee_invoices.success.generated",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("run fee billing scheduler")
@router.post(
    "/fee-billing/run",
    status_code=http_status.HTTP_200_OK,
    summary="Run fee billing scheduler",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("5/minute")
async def run_fee_billing_scheduler(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Generate invoices, send reminders, and process retries for configured projects."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=FINANCE_MANAGEMENT_ADMIN,
    )
    service = FeeSchedulerService(db_connection=db_connection, user_context=user_context)
    data = await service.run_billing_cycle()
    return success_response(
        request=request,
        data=data,
        message_key="fee_invoices.success.scheduler_run",
        custom_code=CustomStatusCode.SUCCESS,
    )
