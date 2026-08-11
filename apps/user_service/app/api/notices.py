"""Admin notice board API (project-scoped)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.notices import (
    CreateNoticeRequest,
    DeleteNoticeRequest,
    NoticeListQuery,
    PinNoticeRequest,
    ReachEstimateQuery,
    UpdateNoticeRequest,
)
from apps.user_service.app.services.notices_service import NoticesService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    PROJECTS_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Notices"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    409: {"description": "Conflict (business rule violation)."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("get project notice summary")
@router.get(
    "/{project_id}/notices/summary",
    status_code=http_status.HTTP_200_OK,
    summary="Notice dashboard summary for a project",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_notice_summary(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return tab counts for the admin notices dashboard."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    data = await service.get_summary(project_id=project_id)
    return success_response(
        request=request,
        message_key="notices.success.summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("list project notices")
@router.get(
    "/{project_id}/notices",
    status_code=http_status.HTTP_200_OK,
    summary="List notices for a project",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_project_notices(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: NoticeListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return paginated notices for admin list tabs."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_notices(project_id=project_id, query=query)
    return list_response(
        request=request,
        items=[item.model_dump(mode="json") for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="notices.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get project notice reach estimate")
@router.get(
    "/{project_id}/notices/reach-estimate",
    status_code=http_status.HTTP_200_OK,
    summary="Estimate notice audience size",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_notice_reach_estimate(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: ReachEstimateQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return approximate recipient count for targeting selections."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    data = await service.get_reach_estimate(project_id=project_id, query=query)
    return success_response(
        request=request,
        message_key="notices.success.reach_estimate_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("get project notice detail")
@router.get(
    "/{project_id}/notices/{notice_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get notice detail for a project",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_notice(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    notice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return full notice detail."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    data = await service.get_notice(project_id=project_id, notice_id=notice_id)
    return success_response(
        request=request,
        message_key="notices.success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("create project notice")
@router.post(
    "/{project_id}/notices",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a notice",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="notices",
    category="NOTICES",
)
async def create_project_notice(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    body: CreateNoticeRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create draft, scheduled, or live notice."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    data = await service.create_notice(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="notices",
        requested_id=data.id,
        description=f"Created notice: {data.display_code}",
        risk_level="medium",
        new_data=data.model_dump(mode="json"),
    )
    return success_response(
        request=request,
        message_key="notices.success.created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("update project notice")
@router.patch(
    "/{project_id}/notices/{notice_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a draft or scheduled notice",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="notices",
    category="NOTICES",
)
async def update_project_notice(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    notice_id: str = Path(...),
    body: UpdateNoticeRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update notice (live notices are immutable)."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    data = await service.update_notice(
        project_id=project_id,
        notice_id=notice_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="notices",
        requested_id=notice_id,
        description=f"Updated notice: {data.display_code}",
        risk_level="medium",
        new_data=data.model_dump(mode="json"),
    )
    return success_response(
        request=request,
        message_key="notices.success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("delete project notice")
@router.post(
    "/{project_id}/notices/{notice_id}/delete",
    status_code=http_status.HTTP_200_OK,
    summary="Soft-delete a notice",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="notices",
    category="NOTICES",
)
async def delete_project_notice(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    notice_id: str = Path(...),
    body: DeleteNoticeRequest | None = Body(None),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Soft-delete notice and unpin if pinned."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    data = await service.delete_notice(
        project_id=project_id,
        notice_id=notice_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="notices",
        requested_id=notice_id,
        description=f"Deleted notice: {data.display_code}",
        risk_level="medium",
        new_data=data.model_dump(mode="json"),
    )
    return success_response(
        request=request,
        message_key="notices.success.deleted",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("restore project notice")
@router.post(
    "/{project_id}/notices/{notice_id}/restore",
    status_code=http_status.HTTP_201_CREATED,
    summary="Restore a deleted notice as a new draft copy",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="notices",
    category="NOTICES",
)
async def restore_project_notice(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    notice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create a new draft notice copied from a deleted notice (original stays deleted)."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    data = await service.restore_notice(project_id=project_id, notice_id=notice_id)
    set_audit_context(
        request,
        user_context,
        table="notices",
        requested_id=data.id,
        description=f"Restored deleted notice {notice_id} as new draft: {data.display_code}",
        risk_level="medium",
        new_data=data.model_dump(mode="json"),
    )
    return success_response(
        request=request,
        message_key="notices.success.restored",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("duplicate project notice")
@router.post(
    "/{project_id}/notices/{notice_id}/duplicate",
    status_code=http_status.HTTP_201_CREATED,
    summary="Duplicate a draft or scheduled notice",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="notices",
    category="NOTICES",
)
async def duplicate_project_notice(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    notice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Duplicate draft/scheduled notice to new draft (not allowed for live)."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    data = await service.duplicate_notice(project_id=project_id, notice_id=notice_id)
    set_audit_context(
        request,
        user_context,
        table="notices",
        requested_id=data.id,
        description=f"Duplicated notice to: {data.display_code}",
        risk_level="low",
        new_data=data.model_dump(mode="json"),
    )
    return success_response(
        request=request,
        message_key="notices.success.duplicated",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("pin project notice")
@router.post(
    "/{project_id}/notices/{notice_id}/pin",
    status_code=http_status.HTTP_200_OK,
    summary="Pin a live notice to banner",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="notice_pins",
    category="NOTICES",
)
async def pin_project_notice(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    notice_id: str = Path(...),
    body: PinNoticeRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Pin live notice to a banner slot."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    data = await service.pin_notice(
        project_id=project_id,
        notice_id=notice_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="notice_pins",
        requested_id=notice_id,
        description=f"Pinned notice: {data.display_code}",
        risk_level="low",
        new_data=data.model_dump(mode="json"),
    )
    return success_response(
        request=request,
        message_key="notices.success.pinned",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("unpin project notice")
@router.post(
    "/{project_id}/notices/{notice_id}/unpin",
    status_code=http_status.HTTP_200_OK,
    summary="Unpin a notice from banner",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="internal",
    compliance_tags=["audit_required"],
    table_name="notice_pins",
    category="NOTICES",
)
async def unpin_project_notice(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    notice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Remove notice from banner slot."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    data = await service.unpin_notice(project_id=project_id, notice_id=notice_id)
    set_audit_context(
        request,
        user_context,
        table="notice_pins",
        requested_id=notice_id,
        description=f"Unpinned notice: {data.display_code}",
        risk_level="low",
        new_data=data.model_dump(mode="json"),
    )
    return success_response(
        request=request,
        message_key="notices.success.unpinned",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("publish due project notices")
@router.post(
    "/{project_id}/notices/publish-due",
    status_code=http_status.HTTP_200_OK,
    summary="Publish scheduled notices that are due (ops/cron)",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
async def publish_due_project_notices(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Promote due scheduled notices to live for this project."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = NoticesService(db_connection=db_connection, user_context=user_context)
    published_ids = await service.publish_due_notices(project_id=project_id)
    return success_response(
        request=request,
        message_key="notices.success.publish_due_completed",
        custom_code=CustomStatusCode.SUCCESS,
        data={"published_notice_ids": published_ids, "count": len(published_ids)},
    )
