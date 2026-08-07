"""Security walk-in API (project-scoped)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.walk_in import CreateWalkInRequest, WalkInListQuery
from apps.user_service.app.services.walk_in_service import WalkInService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import VISITOR_MANAGEMENT_VERIFY
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Walk-in"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("create walk-in visit")
@router.post(
    "/{project_id}/walk-ins",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a walk-in visit",
    description=(
        "Security registers a visitor for one or more flats. Each flat becomes a visit unit "
        "awaiting resident approval."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="walk_in_entries",
    category="VISITOR_PASSES",
)
async def create_walk_in(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateWalkInRequest = Body(...),
):
    """Create a walk-in visit with one or more target flats."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    service = WalkInService(db_connection=db_connection, user_context=user_context)
    data = await service.create_walk_in(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="walk_in_entries",
        requested_id=str(data.get("id", "")),
        description=f"Created walk-in visit in project: {project_id}",
        risk_level="medium",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="walk_in.success.created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("list walk-in visits")
@router.get(
    "/{project_id}/walk-ins",
    status_code=http_status.HTTP_200_OK,
    summary="List walk-in visits for a project",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_walk_ins(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: WalkInListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List walk-in visits for the security app."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    service = WalkInService(db_connection=db_connection, user_context=user_context)
    items = await service.list_project_walk_ins(project_id=project_id, query=query)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="walk_in.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("get walk-in visit")
@router.get(
    "/{project_id}/walk-ins/{walk_in_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get walk-in visit details",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_walk_in(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    walk_in_id: str = Path(..., description="Walk-in entry identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return walk-in detail with visit units and timeline."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    service = WalkInService(db_connection=db_connection, user_context=user_context)
    data = await service.get_project_walk_in(
        project_id=project_id,
        walk_in_entry_id=walk_in_id,
    )
    return success_response(
        request=request,
        message_key="walk_in.success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("mark walk-in entered")
@router.post(
    "/{project_id}/walk-ins/{walk_in_id}/enter",
    status_code=http_status.HTTP_200_OK,
    summary="Mark visitor entered",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="walk_in_entries",
    category="VISITOR_PASSES",
)
async def enter_walk_in(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    walk_in_id: str = Path(..., description="Walk-in entry identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Mark the visitor physically inside (requires at least one approved flat)."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    service = WalkInService(db_connection=db_connection, user_context=user_context)
    data = await service.enter_walk_in(
        project_id=project_id,
        walk_in_entry_id=walk_in_id,
    )
    set_audit_context(
        request,
        user_context,
        table="walk_in_entries",
        requested_id=walk_in_id,
        description=f"Marked walk-in entered: {walk_in_id}",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="walk_in.success.entered",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("mark walk-in exited")
@router.post(
    "/{project_id}/walk-ins/{walk_in_id}/exit",
    status_code=http_status.HTTP_200_OK,
    summary="Mark visitor exited",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="walk_in_entries",
    category="VISITOR_PASSES",
)
async def exit_walk_in(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    walk_in_id: str = Path(..., description="Walk-in entry identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Mark the visitor exited for the whole visit."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=VISITOR_MANAGEMENT_VERIFY,
        request=request,
    )
    service = WalkInService(db_connection=db_connection, user_context=user_context)
    data = await service.exit_walk_in(
        project_id=project_id,
        walk_in_entry_id=walk_in_id,
    )
    set_audit_context(
        request,
        user_context,
        table="walk_in_entries",
        requested_id=walk_in_id,
        description=f"Marked walk-in exited: {walk_in_id}",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="walk_in.success.exited",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
