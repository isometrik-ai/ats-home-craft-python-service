"""CRM dashboard API (aggregated metrics)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.services.dashboard_service import DashboardService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import BUSINESS_DASHBOARD_VIEW
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("get dashboard")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="CRM dashboard summary",
    response_model=None,
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def get_dashboard(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return CRM overview, weekly lead activity, pipeline counts, and my projects.

    Week boundaries and charts use ``organization_members.timezone`` (falls back to UTC).
    """
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=BUSINESS_DASHBOARD_VIEW,
    )
    assert user_context.organization_id is not None
    service = DashboardService(
        db_connection=db_connection,
        organization_id=user_context.organization_id,
        user_id=user_context.user_id,
    )
    data = await service.get_dashboard()
    return success_response(
        request=request,
        message_key="dashboard.success.loaded",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data.model_dump(mode="json"),
    )
