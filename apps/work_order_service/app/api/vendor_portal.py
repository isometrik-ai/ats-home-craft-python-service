"""Vendor portal API (token-scoped)."""

import asyncpg
from fastapi import APIRouter, Body, Depends, Request

from apps.user_service.app.utils.common_utils import handle_api_exceptions
from apps.work_order_service.app.api._helpers import ok_response
from apps.work_order_service.app.app_instance import limiter
from apps.work_order_service.app.dependencies.db import db_conn, db_uow
from apps.work_order_service.app.dependencies.vendor_auth import (
    get_work_order_from_vendor_token,
)
from apps.work_order_service.app.schemas.common import dump_request
from apps.work_order_service.app.schemas.invoices import VendorSubmitInvoiceRequest
from apps.work_order_service.app.schemas.openapi import (
    InvoiceApiResponse,
    VendorInvoiceListApiResponse,
    WorkOrderApiResponse,
)
from apps.work_order_service.app.schemas.work_orders import VendorUpdateWorkOrderRequest
from apps.work_order_service.app.services.invoices_service import InvoicesService
from apps.work_order_service.app.services.work_orders_service import WorkOrdersService
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/vendor", tags=["Work Order — Vendor Portal"])


@handle_api_exceptions("vendor get work order")
@router.get(
    "/work-order",
    response_model=None,
    responses=ok_response(WorkOrderApiResponse, "Vendor-scoped work order."),
)
@limiter.limit("60/minute")
async def vendor_get_work_order(
    request: Request,
    work_order: dict = Depends(get_work_order_from_vendor_token),
):
    """Vendor get work order."""
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=work_order,
    )


@handle_api_exceptions("vendor update work order")
@router.patch(
    "/work-order",
    response_model=None,
    responses=ok_response(WorkOrderApiResponse, "Vendor work order update."),
)
@limiter.limit("30/minute")
async def vendor_update_work_order(
    request: Request,
    body: VendorUpdateWorkOrderRequest = Body(...),
    work_order: dict = Depends(get_work_order_from_vendor_token),
    db_connection: asyncpg.Connection = Depends(db_uow),
):
    """Vendor update work order."""
    allowed = dump_request(body, partial=True)
    allowed.pop("timeline", None)
    allowed["organization_id"] = work_order["organization_id"]
    record = await WorkOrdersService(db_connection).update(
        project_id=work_order["project_id"],
        entity_id=work_order["id"],
        data=allowed,
    )
    return success_response(
        request=request,
        message_key="success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=record,
    )


@handle_api_exceptions("vendor submit invoice")
@router.post(
    "/invoices",
    response_model=None,
    responses=ok_response(InvoiceApiResponse, "Vendor-submitted invoice."),
)
@limiter.limit("20/minute")
async def vendor_submit_invoice(
    request: Request,
    body: VendorSubmitInvoiceRequest = Body(...),
    work_order: dict = Depends(get_work_order_from_vendor_token),
    db_connection: asyncpg.Connection = Depends(db_uow),
):
    """Vendor submit invoice."""
    payload = {
        **dump_request(body),
        "organization_id": work_order["organization_id"],
        "project_id": work_order["project_id"],
        "work_order_id": work_order["id"],
        "company_id": work_order.get("company_id") or body.company_id,
    }
    record = await InvoicesService(db_connection).create(
        project_id=work_order["project_id"],
        data=payload,
        source="vendor_portal",
    )
    return success_response(
        request=request,
        message_key="success.created",
        custom_code=CustomStatusCode.CREATED,
        data=record,
    )


@handle_api_exceptions("vendor list invoices")
@router.get(
    "/invoices",
    response_model=None,
    responses=ok_response(VendorInvoiceListApiResponse, "Vendor invoices for work order."),
)
@limiter.limit("60/minute")
async def vendor_list_invoices(
    request: Request,
    work_order: dict = Depends(get_work_order_from_vendor_token),
    db_connection: asyncpg.Connection = Depends(db_conn),
):
    """Vendor list invoices."""
    from apps.work_order_service.app.db.repositories.invoices_repository import (
        InvoicesRepository,
    )

    repo = InvoicesRepository(db_connection)
    items, total = await repo.list(
        organization_id=work_order["organization_id"],
        project_id=work_order["project_id"],
        work_order_id=work_order["id"],
    )
    return success_response(
        request=request,
        message_key="success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data={"items": items, "total": total},
    )
