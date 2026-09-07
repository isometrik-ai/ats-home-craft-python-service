"""Audit and webhook delivery logs API."""

import asyncpg
from fastapi import APIRouter, Depends, Path, Query, Request

from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from apps.work_order_service.app.api._helpers import ok_response
from apps.work_order_service.app.app_instance import limiter
from apps.work_order_service.app.db.repositories.integration_repository import (
    IntegrationRepository,
)
from apps.work_order_service.app.dependencies.db import db_conn
from apps.work_order_service.app.schemas.openapi import (
    AuditEventListApiResponse,
    WebhookDeliveryListApiResponse,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import WORK_ORDER_MANAGEMENT_VIEW
from libs.shared_utils.response_factory import list_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Work Order — Logs"])


@handle_api_exceptions("list audit events")
@router.get(
    "/{project_id}/audit-events",
    response_model=None,
    responses=ok_response(AuditEventListApiResponse, "Paginated audit events."),
)
@limiter.limit("100/minute")
async def list_audit_events(
    request: Request,
    project_id: str = Path(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    entity: str | None = Query(None),
    entity_id: str | None = Query(None),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List audit events."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    items, total = await IntegrationRepository(db_connection).list_audit_events(
        organization_id=ctx.organization_id,
        project_id=project_id,
        page=page,
        page_size=page_size,
        entity=entity,
        entity_id=entity_id,
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


@handle_api_exceptions("list webhook deliveries")
@router.get(
    "/{project_id}/webhook-deliveries",
    response_model=None,
    responses=ok_response(WebhookDeliveryListApiResponse, "Paginated webhook deliveries."),
)
@limiter.limit("100/minute")
async def list_webhook_deliveries(
    request: Request,
    project_id: str = Path(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List webhook deliveries."""
    ctx = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=WORK_ORDER_MANAGEMENT_VIEW,
        request=request,
    )
    items, total = await IntegrationRepository(db_connection).list_webhook_deliveries(
        organization_id=ctx.organization_id,
        project_id=project_id,
        page=page,
        page_size=page_size,
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
