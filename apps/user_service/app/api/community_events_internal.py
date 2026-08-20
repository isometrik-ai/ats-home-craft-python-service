"""Internal ops endpoints for community events jobs."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_uow
from apps.user_service.app.jobs.complete_past_community_events import (
    complete_past_community_events,
)
from apps.user_service.app.jobs.send_community_event_reminders import (
    send_community_event_reminders,
)
from apps.user_service.app.utils.common_utils import (
    handle_api_exceptions,
    require_super_admin,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/internal/community-events", tags=["Community Events (Internal)"])


@handle_api_exceptions("complete past community events")
@router.post(
    "/complete-past",
    status_code=http_status.HTTP_200_OK,
    summary="Mark past published events as completed (superadmin/cron)",
)
@limiter.limit("10/minute")
async def complete_past_community_events_internal(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Auto-complete events whose end date/time has passed."""
    await require_super_admin(current_user)
    completed_ids = await complete_past_community_events(db_connection)
    return success_response(
        request=request,
        message_key="community_events.success.complete_past_completed",
        custom_code=CustomStatusCode.SUCCESS,
        data={"completed_event_ids": completed_ids, "count": len(completed_ids)},
    )


@handle_api_exceptions("send community event reminders")
@router.post(
    "/send-reminders",
    status_code=http_status.HTTP_200_OK,
    summary="Send 24h event reminders to confirmed bookers (superadmin/cron)",
)
@limiter.limit("10/minute")
async def send_community_event_reminders_internal(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Push reminders for events starting in ~24 hours."""
    await require_super_admin(current_user)
    result = await send_community_event_reminders(db_connection)
    return success_response(
        request=request,
        message_key="community_events.success.reminders_sent",
        custom_code=CustomStatusCode.SUCCESS,
        data=result,
    )
