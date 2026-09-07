"""Payments API."""

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
from apps.work_order_service.app.schemas.common import dump_request
from apps.work_order_service.app.schemas.invoices import (
    CreatePaymentRequest,
    UpdatePaymentRequest,
)
from apps.work_order_service.app.schemas.openapi import (
    DeleteIdApiResponse,
    PaymentApiResponse,
    PaymentListApiResponse,
)
from apps.work_order_service.app.services.payments_service import PaymentsService
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    WORK_ORDER_MANAGEMENT_PAY,
    WORK_ORDER_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Work Order — Payments"])


@handle_api_exceptions("list payments")
@router.get(
    "/{project_id}/payments",
    response_model=None,
    responses=ok_response(PaymentListApiResponse, "Paginated payments."),
)
@limiter.limit("100/minute")
async def list_payments(
    request: Request,
    project_id: str = Path(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    invoice_id: str | None = Query(None),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List payments."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    items, total = await PaymentsService(db_connection, ctx).list(
        project_id=project_id,
        page=page,
        page_size=page_size,
        invoice_id=invoice_id,
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


@handle_api_exceptions("get payment")
@router.get(
    "/{project_id}/payments/{payment_id}",
    response_model=None,
    responses=ok_response(PaymentApiResponse, "Payment detail."),
)
@limiter.limit("100/minute")
async def get_payment(
    request: Request,
    project_id: str = Path(...),
    payment_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get payment."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    record = await PaymentsService(db_connection, ctx).get(
        project_id=project_id, entity_id=payment_id
    )
    if not record:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=record,
    )


@handle_api_exceptions("create payment")
@router.post(
    "/{project_id}/payments",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=created_response(PaymentApiResponse, "Created payment."),
)
@limiter.limit("60/minute")
async def create_payment(
    request: Request,
    project_id: str = Path(...),
    body: CreatePaymentRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create payment."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_PAY,
        request=request,
    )
    record = await PaymentsService(db_connection, ctx).create(
        project_id=project_id, data=dump_request(body)
    )
    return success_response(
        request=request,
        message_key="success.created",
        custom_code=CustomStatusCode.CREATED,
        data=record,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("update payment")
@router.patch(
    "/{project_id}/payments/{payment_id}",
    response_model=None,
    responses=ok_response(PaymentApiResponse, "Updated payment."),
)
@limiter.limit("60/minute")
async def update_payment(
    request: Request,
    project_id: str = Path(...),
    payment_id: str = Path(...),
    body: UpdatePaymentRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update payment."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_PAY,
        request=request,
    )
    record = await PaymentsService(db_connection, ctx).update(
        project_id=project_id,
        entity_id=payment_id,
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


@handle_api_exceptions("delete payment")
@router.delete(
    "/{project_id}/payments/{payment_id}",
    response_model=None,
    responses=ok_response(DeleteIdApiResponse, "Deleted payment."),
)
@limiter.limit("60/minute")
async def delete_payment(
    request: Request,
    project_id: str = Path(...),
    payment_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete payment."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_PAY,
        request=request,
    )
    ok = await PaymentsService(db_connection, ctx).delete(
        project_id=project_id, entity_id=payment_id
    )
    if not ok:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.deleted",
        custom_code=CustomStatusCode.SUCCESS,
        data={"id": payment_id},
    )
