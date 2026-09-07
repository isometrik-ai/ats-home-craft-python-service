"""Vendor invoices API."""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from apps.work_order_service.app.api._helpers import created_response, ok_response
from apps.work_order_service.app.app_instance import limiter
from apps.work_order_service.app.dependencies.db import db_conn, db_uow
from apps.work_order_service.app.schemas.common import (
    TimelineEventRequest,
    dump_request,
)
from apps.work_order_service.app.schemas.invoices import (
    CreateInvoiceRequest,
    UpdateInvoiceRequest,
)
from apps.work_order_service.app.schemas.openapi import (
    DeleteIdApiResponse,
    InvoiceApiResponse,
    InvoiceListApiResponse,
    InvoiceTimelineApiResponse,
)
from apps.work_order_service.app.services.invoices_service import InvoicesService
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    WORK_ORDER_MANAGEMENT_APPROVE,
    WORK_ORDER_MANAGEMENT_EDIT,
    WORK_ORDER_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Work Order — Invoices"])


@handle_api_exceptions("list invoices")
@router.get(
    "/{project_id}/invoices",
    response_model=None,
    responses=ok_response(InvoiceListApiResponse, "Paginated vendor invoices."),
)
@limiter.limit("100/minute")
async def list_invoices(
    request: Request,
    project_id: str = Path(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    work_order_id: str | None = Query(None),
    status: str | None = Query(None),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List invoices."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    items, total = await InvoicesService(db_connection, ctx).list(
        project_id=project_id,
        page=page,
        page_size=page_size,
        work_order_id=work_order_id,
        status=status,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        message_key="success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get invoice")
@router.get(
    "/{project_id}/invoices/{invoice_id}",
    response_model=None,
    responses=ok_response(InvoiceApiResponse, "Invoice detail."),
)
@limiter.limit("100/minute")
async def get_invoice(
    request: Request,
    project_id: str = Path(...),
    invoice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get invoice."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    record = await InvoicesService(db_connection, ctx).get(
        project_id=project_id, entity_id=invoice_id
    )
    if not record:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=record,
    )


@handle_api_exceptions("create invoice")
@router.post(
    "/{project_id}/invoices",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=created_response(InvoiceApiResponse, "Created invoice."),
)
@limiter.limit("60/minute")
async def create_invoice(
    request: Request,
    project_id: str = Path(...),
    body: CreateInvoiceRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create invoice."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    record = await InvoicesService(db_connection, ctx).create(
        project_id=project_id, data=dump_request(body)
    )
    return success_response(
        request=request,
        message_key="success.created",
        custom_code=CustomStatusCode.CREATED,
        data=record,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("update invoice")
@router.patch(
    "/{project_id}/invoices/{invoice_id}",
    response_model=None,
    responses=ok_response(InvoiceApiResponse, "Updated invoice."),
)
@limiter.limit("60/minute")
async def update_invoice(
    request: Request,
    project_id: str = Path(...),
    invoice_id: str = Path(...),
    body: UpdateInvoiceRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update invoice."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_APPROVE,
        request=request,
    )
    record = await InvoicesService(db_connection, ctx).update(
        project_id=project_id,
        entity_id=invoice_id,
        data=dump_request(body, partial=True),
    )
    if not record:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=record,
    )


@handle_api_exceptions("delete invoice")
@router.delete(
    "/{project_id}/invoices/{invoice_id}",
    response_model=None,
    responses=ok_response(DeleteIdApiResponse, "Deleted invoice."),
)
@limiter.limit("60/minute")
async def delete_invoice(
    request: Request,
    project_id: str = Path(...),
    invoice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete invoice."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    ok = await InvoicesService(db_connection, ctx).delete(
        project_id=project_id, entity_id=invoice_id
    )
    if not ok:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.deleted",
        custom_code=CustomStatusCode.SUCCESS,
        data={"id": invoice_id},
    )


@handle_api_exceptions("append invoice timeline")
@router.post(
    "/{project_id}/invoices/{invoice_id}/timeline",
    response_model=None,
    responses=ok_response(InvoiceTimelineApiResponse, "Updated invoice timeline."),
)
@limiter.limit("60/minute")
async def append_invoice_timeline(
    request: Request,
    project_id: str = Path(...),
    invoice_id: str = Path(...),
    body: TimelineEventRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Append invoice timeline."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_APPROVE,
        request=request,
    )
    timeline = await InvoicesService(db_connection, ctx).append_timeline(
        project_id=project_id,
        entity_id=invoice_id,
        event=dump_request(body),
    )
    if timeline is None:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=timeline,
    )
