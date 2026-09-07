"""Work orders API."""

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
from apps.work_order_service.app.schemas.openapi import (
    DeleteIdApiResponse,
    WorkOrderApiResponse,
    WorkOrderListApiResponse,
    WorkOrderTimelineApiResponse,
)
from apps.work_order_service.app.schemas.work_orders import (
    CreateWorkOrderRequest,
    UpdateWorkOrderRequest,
)
from apps.work_order_service.app.services.work_orders_service import WorkOrdersService
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    WORK_ORDER_MANAGEMENT_EDIT,
    WORK_ORDER_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Work Order — Work Orders"])


@handle_api_exceptions("list work orders")
@router.get(
    "/{project_id}/work-orders",
    response_model=None,
    responses=ok_response(WorkOrderListApiResponse, "Paginated work orders."),
)
@limiter.limit("100/minute")
async def list_work_orders(
    request: Request,
    project_id: str = Path(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    state: str | None = Query(None),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List work orders."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    items, total = await WorkOrdersService(db_connection, ctx).list(
        project_id=project_id, page=page, page_size=page_size, state=state
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


@handle_api_exceptions("get work order")
@router.get(
    "/{project_id}/work-orders/{work_order_id}",
    response_model=None,
    responses=ok_response(WorkOrderApiResponse, "Work order detail."),
)
@limiter.limit("100/minute")
async def get_work_order(
    request: Request,
    project_id: str = Path(...),
    work_order_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Get work order."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    record = await WorkOrdersService(db_connection, ctx).get(
        project_id=project_id, entity_id=work_order_id
    )
    if not record:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=record,
    )


@handle_api_exceptions("create work order")
@router.post(
    "/{project_id}/work-orders",
    status_code=http_status.HTTP_201_CREATED,
    response_model=None,
    responses=created_response(WorkOrderApiResponse, "Created work order."),
)
@limiter.limit("60/minute")
async def create_work_order(
    request: Request,
    project_id: str = Path(...),
    body: CreateWorkOrderRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create work order."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    record = await WorkOrdersService(db_connection, ctx).create(
        project_id=project_id, data=dump_request(body)
    )
    return success_response(
        request=request,
        message_key="success.created",
        custom_code=CustomStatusCode.CREATED,
        data=record,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("update work order")
@router.patch(
    "/{project_id}/work-orders/{work_order_id}",
    response_model=None,
    responses=ok_response(WorkOrderApiResponse, "Updated work order."),
)
@limiter.limit("60/minute")
async def update_work_order(
    request: Request,
    project_id: str = Path(...),
    work_order_id: str = Path(...),
    body: UpdateWorkOrderRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update work order."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    record = await WorkOrdersService(db_connection, ctx).update(
        project_id=project_id,
        entity_id=work_order_id,
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


@handle_api_exceptions("delete work order")
@router.delete(
    "/{project_id}/work-orders/{work_order_id}",
    response_model=None,
    responses=ok_response(DeleteIdApiResponse, "Deleted work order."),
)
@limiter.limit("60/minute")
async def delete_work_order(
    request: Request,
    project_id: str = Path(...),
    work_order_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Delete work order."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    ok = await WorkOrdersService(db_connection, ctx).delete(
        project_id=project_id, entity_id=work_order_id
    )
    if not ok:
        raise NotFoundException(message_key="errors.not_found")
    return success_response(
        request=request,
        message_key="success.deleted",
        custom_code=CustomStatusCode.SUCCESS,
        data={"id": work_order_id},
    )


@handle_api_exceptions("append work order timeline")
@router.post(
    "/{project_id}/work-orders/{work_order_id}/timeline",
    response_model=None,
    responses=ok_response(WorkOrderTimelineApiResponse, "Updated work order timeline."),
)
@limiter.limit("60/minute")
async def append_work_order_timeline(
    request: Request,
    project_id: str = Path(...),
    work_order_id: str = Path(...),
    body: TimelineEventRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Append work order timeline."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
        request=request,
    )
    timeline = await WorkOrdersService(db_connection, ctx).append_timeline(
        project_id=project_id,
        entity_id=work_order_id,
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
