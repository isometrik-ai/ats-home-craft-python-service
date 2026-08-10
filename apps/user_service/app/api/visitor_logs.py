"""Visitor logs admin API."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.schemas.visitor_logs import (
    VisitorLogDetailApiResponse,
    VisitorLogListApiResponse,
    VisitorLogOverviewApiResponse,
    VisitorLogOverviewQuery,
    VisitorLogQuery,
)
from apps.user_service.app.services.visitor_logs_service import VisitorLogsService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    ensure_staff_project_access_optional,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import VISITOR_MANAGEMENT_VIEW
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/visitor-logs", tags=["Visitor Logs"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}

LIST_SUCCESS_RESPONSES: dict[int | str, dict] = {
    **COMMON_ERROR_RESPONSES,
    http_status.HTTP_200_OK: {
        "model": VisitorLogListApiResponse,
        "description": (
            "Paginated visitor log rows from passes and walk-ins across the full visit lifecycle."
        ),
    },
}

OVERVIEW_SUCCESS_RESPONSES: dict[int | str, dict] = {
    **COMMON_ERROR_RESPONSES,
    http_status.HTTP_200_OK: {
        "model": VisitorLogOverviewApiResponse,
        "description": "Overview card metrics for the selected date range.",
    },
}

DETAIL_SUCCESS_RESPONSES: dict[int | str, dict] = {
    **COMMON_ERROR_RESPONSES,
    http_status.HTTP_200_OK: {
        "model": VisitorLogDetailApiResponse,
        "description": (
            "Pass detail with timeline when the id is a pass; "
            "walk-in detail when the id is a walk-in entry."
        ),
    },
}


@handle_api_exceptions("list visitor logs")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List visitor logs",
    response_model=None,
    responses=LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_visitor_logs(
    request: Request,
    query: VisitorLogQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return paginated visitor logs for the organization."""
    user_context = await ensure_staff_project_access_optional(
        current_user=current_user,
        db_connection=db_connection,
        project_id=query.project_id,
        permission_codes=VISITOR_MANAGEMENT_VIEW,
        request=request,
    )
    service = VisitorLogsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items, total = await service.list_logs(
        start_at=query.start_at,
        end_at=query.end_at,
        search=query.search,
        bucket=query.bucket.value if query.bucket else None,
        visitor_type=query.visitor_type.value if query.visitor_type else None,
        pass_type=query.pass_type.value if query.pass_type else None,
        entry_method=query.entry_method.value if query.entry_method else None,
        access_status=query.access_status.value if query.access_status else None,
        tower_id=query.tower_id,
        guard_user_id=query.guard_user_id,
        project_id=query.project_id,
        unit_id=query.unit_id,
        page=query.page,
        page_size=query.page_size,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="visitor_logs.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get visitor log overview")
@router.get(
    "/overview",
    status_code=http_status.HTTP_200_OK,
    summary="Get visitor log overview",
    response_model=None,
    responses=OVERVIEW_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_visitor_log_overview(
    request: Request,
    query: VisitorLogOverviewQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return overview card metrics for visitor logs."""
    user_context = await ensure_staff_project_access_optional(
        current_user=current_user,
        db_connection=db_connection,
        project_id=query.project_id,
        permission_codes=VISITOR_MANAGEMENT_VIEW,
        request=request,
    )
    service = VisitorLogsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    result = await service.get_overview(
        start_at=query.start_at,
        end_at=query.end_at,
        project_id=query.project_id,
        unit_id=query.unit_id,
    )
    return success_response(
        request=request,
        message_key="visitor_logs.success.overview_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=result,
    )


@handle_api_exceptions("get visitor log detail")
@router.get(
    "/{pass_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get visitor log detail",
    response_model=None,
    responses=DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_visitor_log_detail(
    request: Request,
    pass_id: str = Path(..., description="Pass or walk-in entry UUID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return pass detail with full timeline for admin."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=VISITOR_MANAGEMENT_VIEW,
        request=request,
    )
    service = VisitorLogsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    result = await service.get_log_detail(pass_id=pass_id)
    return success_response(
        request=request,
        message_key="visitor_logs.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=result,
    )
