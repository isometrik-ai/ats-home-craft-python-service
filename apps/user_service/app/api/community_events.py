"""Admin community events API (project-scoped)."""

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request, Response
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.community_events import (
    CancelCommunityEventRequest,
    CommunityEventBookingListQuery,
    CommunityEventExportQuery,
    CommunityEventListQuery,
    CreateCommunityEventRequest,
    MarkBookingPaidRequest,
    MarkBookingWaivedRequest,
    UpdateCommunityEventRequest,
)
from apps.user_service.app.services.community_events_service import (
    CommunityEventsService,
)
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

router = APIRouter(prefix="/projects", tags=["Community Events"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized."},
    403: {"description": "Forbidden."},
    404: {"description": "Not found."},
    409: {"description": "Conflict."},
    422: {"description": "Validation error."},
}


@handle_api_exceptions("get community events summary")
@router.get(
    "/{project_id}/community-events/summary",
    status_code=http_status.HTTP_200_OK,
    summary="Community events dashboard summary",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_community_events_summary(
    request: Request,
    project_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return summary cards and tab counts."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    data = await service.get_summary(project_id=project_id)
    return success_response(
        request=request,
        message_key="community_events.success.summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(),
    )


@handle_api_exceptions("list community events")
@router.get(
    "/{project_id}/community-events",
    status_code=http_status.HTTP_200_OK,
    summary="List community events",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_community_events(
    request: Request,
    project_id: str = Path(...),
    query: CommunityEventListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Paginated admin event list."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_events(project_id=project_id, query=query)
    return list_response(
        request=request,
        items=[item.model_dump(mode="json") for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="community_events.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("export community events")
@router.get(
    "/{project_id}/community-events/export",
    status_code=http_status.HTTP_200_OK,
    summary="Export community events CSV",
    response_model=None,
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
async def export_community_events(
    request: Request,
    project_id: str = Path(...),
    query: CommunityEventExportQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Export filtered events as CSV."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    csv_text = await service.export_events_csv(project_id=project_id, query=query)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="community-events-{project_id}.csv"'
        },
    )


@handle_api_exceptions("get community event detail")
@router.get(
    "/{project_id}/community-events/{event_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get community event detail",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_community_event(
    request: Request,
    project_id: str = Path(...),
    event_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Event detail for admin."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    data = await service.get_event(project_id=project_id, event_id=event_id)
    return success_response(
        request=request,
        message_key="community_events.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("create community event")
@router.post(
    "/{project_id}/community-events",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create community event",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    table_name="community_events",
    category="COMMUNITY_EVENTS",
)
async def create_community_event(
    request: Request,
    project_id: str = Path(...),
    body: CreateCommunityEventRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create draft or published event."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    data = await service.create_event(project_id=project_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="community_events",
        requested_id=data.id,
        description=f"Created community event: {data.display_code}",
        new_data=data.model_dump(mode="json"),
    )
    return success_response(
        request=request,
        message_key="community_events.success.created",
        custom_code=CustomStatusCode.CREATED,
        data=data.model_dump(mode="json"),
        status_code=http_status.HTTP_201_CREATED,
    )


@handle_api_exceptions("update community event")
@router.patch(
    "/{project_id}/community-events/{event_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update community event",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    table_name="community_events",
    category="COMMUNITY_EVENTS",
)
async def update_community_event(
    request: Request,
    project_id: str = Path(...),
    event_id: str = Path(...),
    body: UpdateCommunityEventRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Update event."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    data = await service.update_event(project_id=project_id, event_id=event_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="community_events",
        requested_id=event_id,
        description=f"Updated community event: {event_id}",
        new_data=data.model_dump(mode="json"),
    )
    return success_response(
        request=request,
        message_key="community_events.success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("publish community event")
@router.post(
    "/{project_id}/community-events/{event_id}/publish",
    status_code=http_status.HTTP_200_OK,
    summary="Publish community event",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
async def publish_community_event(
    request: Request,
    project_id: str = Path(...),
    event_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Publish draft event."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    data = await service.publish_event(project_id=project_id, event_id=event_id)
    return success_response(
        request=request,
        message_key="community_events.success.published",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("cancel community event")
@router.post(
    "/{project_id}/community-events/{event_id}/cancel",
    status_code=http_status.HTTP_200_OK,
    summary="Cancel community event",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
async def cancel_community_event(
    request: Request,
    project_id: str = Path(...),
    event_id: str = Path(...),
    body: CancelCommunityEventRequest = Body(default_factory=CancelCommunityEventRequest),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Cancel event."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    data = await service.cancel_event(project_id=project_id, event_id=event_id, body=body)
    return success_response(
        request=request,
        message_key="community_events.success.cancelled",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("complete community event")
@router.post(
    "/{project_id}/community-events/{event_id}/complete",
    status_code=http_status.HTTP_200_OK,
    summary="Complete community event",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
async def complete_community_event(
    request: Request,
    project_id: str = Path(...),
    event_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Mark event completed."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    data = await service.complete_event(project_id=project_id, event_id=event_id)
    return success_response(
        request=request,
        message_key="community_events.success.completed",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("delete community event")
@router.post(
    "/{project_id}/community-events/{event_id}/delete",
    status_code=http_status.HTTP_200_OK,
    summary="Soft delete community event",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
async def delete_community_event(
    request: Request,
    project_id: str = Path(...),
    event_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Soft delete event."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    data = await service.delete_event(project_id=project_id, event_id=event_id)
    return success_response(
        request=request,
        message_key="community_events.success.deleted",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("restore community event")
@router.post(
    "/{project_id}/community-events/{event_id}/restore",
    status_code=http_status.HTTP_200_OK,
    summary="Restore deleted community event",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
async def restore_community_event(
    request: Request,
    project_id: str = Path(...),
    event_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Restore soft-deleted event."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    data = await service.restore_event(project_id=project_id, event_id=event_id)
    return success_response(
        request=request,
        message_key="community_events.success.restored",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("list community event bookings")
@router.get(
    "/{project_id}/community-events/{event_id}/bookings",
    status_code=http_status.HTTP_200_OK,
    summary="List event bookings",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_community_event_bookings(
    request: Request,
    project_id: str = Path(...),
    event_id: str = Path(...),
    query: CommunityEventBookingListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Bookings for an event."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_bookings(
        project_id=project_id,
        event_id=event_id,
        query=query,
    )
    return list_response(
        request=request,
        items=[item.model_dump(mode="json") for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="community_events.success.bookings_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("export community event bookings")
@router.get(
    "/{project_id}/community-events/{event_id}/bookings/export",
    status_code=http_status.HTTP_200_OK,
    summary="Export event bookings CSV",
    response_model=None,
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
async def export_community_event_bookings(
    request: Request,
    project_id: str = Path(...),
    event_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Export bookings CSV."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_VIEW,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    csv_text = await service.export_bookings_csv(project_id=project_id, event_id=event_id)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="event-bookings-{event_id}.csv"'},
    )


@handle_api_exceptions("mark community event booking paid")
@router.post(
    "/{project_id}/community-events/{event_id}/bookings/{booking_id}/mark-paid",
    status_code=http_status.HTTP_200_OK,
    summary="Mark booking paid",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
@audit_api_call(
    action_type="UPDATE",
    table_name="community_event_bookings",
    category="COMMUNITY_EVENTS",
)
async def mark_community_event_booking_paid(
    request: Request,
    project_id: str = Path(...),
    event_id: str = Path(...),
    booking_id: str = Path(...),
    body: MarkBookingPaidRequest = Body(default_factory=MarkBookingPaidRequest),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Mark booking paid (manual/offline)."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    data = await service.mark_booking_paid(
        project_id=project_id,
        event_id=event_id,
        booking_id=booking_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="community_event_bookings",
        requested_id=booking_id,
        description=f"Marked booking paid: {booking_id}",
        new_data=data.model_dump(mode="json"),
    )
    return success_response(
        request=request,
        message_key="community_events.success.booking_marked_paid",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("mark community event booking waived")
@router.post(
    "/{project_id}/community-events/{event_id}/bookings/{booking_id}/mark-waived",
    status_code=http_status.HTTP_200_OK,
    summary="Mark booking payment waived",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def mark_community_event_booking_waived(
    request: Request,
    project_id: str = Path(...),
    event_id: str = Path(...),
    booking_id: str = Path(...),
    body: MarkBookingWaivedRequest = Body(default_factory=MarkBookingWaivedRequest),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Waive booking payment."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=PROJECTS_MANAGEMENT_EDIT,
        request=request,
    )
    service = CommunityEventsService(db_connection=db_connection, user_context=user_context)
    data = await service.mark_booking_waived(
        project_id=project_id,
        event_id=event_id,
        booking_id=booking_id,
        body=body,
    )
    return success_response(
        request=request,
        message_key="community_events.success.booking_marked_waived",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )
