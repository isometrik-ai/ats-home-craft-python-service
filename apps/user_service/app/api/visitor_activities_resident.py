"""Resident visitor activities API (unit-scoped)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.schemas.visitor_activities import (
    ResidentVisitorActivityListQuery,
)
from apps.user_service.app.services.resident_visitor_activities_service import (
    ResidentVisitorActivitiesService,
)
from apps.user_service.app.utils.common_utils import (
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/units", tags=["Visitor Activities (Resident)"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("list resident visitor activities")
@router.get(
    "/{unit_id}/visitor-activities",
    status_code=http_status.HTTP_200_OK,
    summary="List visitor activities for a flat",
    description=(
        "Returns a unified feed of passes, walk-ins, and daily help visits for the "
        "resident's flat. Private passes created by other household members are hidden."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_resident_visitor_activities(
    request: Request,
    unit_id: str = Path(..., description="Flat identifier (UUID string)."),
    query: ResidentVisitorActivityListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List visitor activities scoped to one resident flat."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user,
        db_connection,
        request=request,
    )
    service = ResidentVisitorActivitiesService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items, total = await service.list_activities(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        start_at=query.start_at,
        end_at=query.end_at,
        bucket=query.bucket.value if query.bucket else None,
        activity_type=query.type.value if query.type else None,
        page=query.page,
        page_size=query.page_size,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="visitor_activities.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get resident visitor activity detail")
@router.get(
    "/{unit_id}/visitor-activities/{activity_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get visitor activity detail for a flat",
    description=(
        "Returns pass or walk-in detail for one activity on the resident's flat. "
        "Private passes created by other household members are not accessible."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_resident_visitor_activity_detail(
    request: Request,
    unit_id: str = Path(..., description="Flat identifier (UUID string)."),
    activity_id: str = Path(..., description="Pass or walk-in entry identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one visitor activity detail scoped to the resident flat."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user,
        db_connection,
        request=request,
    )
    service = ResidentVisitorActivitiesService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.get_activity_detail(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        activity_id=activity_id,
    )
    return success_response(
        request=request,
        message_key="visitor_activities.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
