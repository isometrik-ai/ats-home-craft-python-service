"""Manual scheduler trigger API."""

import asyncpg
from fastapi import APIRouter, Depends, Header, Path, Request

from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from apps.work_order_service.app.api._helpers import ok_response
from apps.work_order_service.app.app_instance import limiter
from apps.work_order_service.app.config.app_settings import app_settings
from apps.work_order_service.app.dependencies.db import db_uow
from apps.work_order_service.app.schemas.openapi import SchedulerRunApiResponse
from apps.work_order_service.app.services.scheduler_service import SchedulerService
from apps.work_order_service.app.services.scheduler_worker import run_scheduler_once
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import WORK_ORDER_MANAGEMENT_EDIT
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Work Order — Scheduler"])


@handle_api_exceptions("run scheduler")
@router.post(
    "/{project_id}/scheduler/run",
    response_model=None,
    responses=ok_response(SchedulerRunApiResponse, "Scheduler run summary counts."),
)
@limiter.limit("10/minute")
async def run_scheduler(
    request: Request,
    project_id: str = Path(...),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Run scheduler."""
    internal = app_settings.scheduler.internal_token
    if internal and x_internal_token == internal:
        result = await run_scheduler_once()
    else:
        ctx = await ensure_staff_project_access(
            current_user=current_user,
            db_connection=db_connection,
            project_id=project_id,
            permission_codes=WORK_ORDER_MANAGEMENT_EDIT,
            request=request,
        )
        if not ctx.organization_id:
            raise ValidationException(message_key="auth.errors.unauthorized")
        service = SchedulerService(db_connection)
        result = await service.generate_due_work_orders(ctx.organization_id)
    return success_response(
        request=request,
        message_key="success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=result,
    )
