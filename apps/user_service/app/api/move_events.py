"""Move events admin API."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.move_events import (
    CreateMoveEventRequest,
    MoveEventListQuery,
    UpdateMoveEventRequest,
)
from apps.user_service.app.services.move_events_service import MoveEventsService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    CONTACTS_MANAGEMENT_CREATE,
    CONTACTS_MANAGEMENT_DELETE,
    CONTACTS_MANAGEMENT_EDIT,
    CONTACTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/move-events", tags=["Move Events"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("list move events")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List move events",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_move_events(
    request: Request,
    query: MoveEventListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return paginated move-in / move-out records for the organization."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = MoveEventsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items, total = await service.list_move_events(
        bucket=query.bucket.value if query.bucket else None,
        search=query.search,
        unit_id=query.unit_id,
        project_id=query.project_id,
        page=query.page,
        page_size=query.page_size,
    )
    return list_response(
        request=request,
        items=[item.model_dump() for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="move_events.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("create move event")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Record a move-in or move-out",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="move_events",
    category="MOVE_EVENTS",
)
async def create_move_event(
    request: Request,
    body: CreateMoveEventRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Record a move event and sync contact_units occupancy."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_CREATE,
        request=request,
    )
    service = MoveEventsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    created = await service.create_move_event(body)
    set_audit_context(
        request,
        user_context,
        table="move_events",
        requested_id=str(created.id),
        description=f"Created move event: {created.id}",
        risk_level="high",
        new_data=created.model_dump(),
    )
    return success_response(
        request=request,
        data=created.model_dump(),
        message_key="move_events.success.created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("get move event")
@router.get(
    "/{move_event_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get move event details",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_move_event(
    request: Request,
    move_event_id: str = Path(..., description="Move event ID"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one move event with joined unit and contact display fields."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = MoveEventsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    item = await service.get_move_event(move_event_id)
    return success_response(
        request=request,
        data=item.model_dump(),
        message_key="move_events.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("update move event")
@router.patch(
    "/{move_event_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update move event",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="move_events",
    category="MOVE_EVENTS",
)
async def update_move_event(
    request: Request,
    move_event_id: str = Path(..., description="Move event ID"),
    body: UpdateMoveEventRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Correct move event date, fee, notes, or documents."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = MoveEventsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    updated = await service.update_move_event(move_event_id, body)
    set_audit_context(
        request,
        user_context,
        table="move_events",
        requested_id=move_event_id,
        description=f"Updated move event: {move_event_id}",
        risk_level="medium",
        new_data=updated.model_dump(),
    )
    return success_response(
        request=request,
        data=updated.model_dump(),
        message_key="move_events.success.updated",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("delete move event")
@router.delete(
    "/{move_event_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Void move event",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "soc2_audit", "audit_required"],
    table_name="move_events",
    category="MOVE_EVENTS",
)
async def delete_move_event(
    request: Request,
    move_event_id: str = Path(..., description="Move event ID"),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Soft-void a move event and re-derive occupancy from prior moves."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=CONTACTS_MANAGEMENT_DELETE,
        request=request,
    )
    service = MoveEventsService(
        db_connection=db_connection,
        user_context=user_context,
    )
    deleted = await service.delete_move_event(move_event_id)
    set_audit_context(
        request,
        user_context,
        table="move_events",
        requested_id=move_event_id,
        description=f"Deleted move event: {move_event_id}",
        risk_level="high",
        old_data=deleted.model_dump(),
    )
    return success_response(
        request=request,
        data=deleted.model_dump(),
        message_key="move_events.success.deleted",
        custom_code=CustomStatusCode.SUCCESS,
    )
