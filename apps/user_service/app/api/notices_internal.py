"""Internal ops endpoints for notice board jobs."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_uow
from apps.user_service.app.jobs.publish_scheduled_notices import (
    expire_notice_pins,
    publish_scheduled_notices,
)
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/internal/notices", tags=["Notices (Internal)"])


@handle_api_exceptions("publish due notices globally")
@router.post(
    "/publish-due",
    status_code=http_status.HTTP_200_OK,
    summary="Publish all due scheduled notices (cron)",
)
@limiter.limit("10/minute")
async def publish_due_notices_internal(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
):
    """Promote all due scheduled notices org-wide."""
    published_ids = await publish_scheduled_notices(db_connection)
    return success_response(
        request=request,
        message_key="notices.success.publish_due_completed",
        custom_code=CustomStatusCode.SUCCESS,
        data={"published_notice_ids": published_ids, "count": len(published_ids)},
    )


@handle_api_exceptions("expire notice pins globally")
@router.post(
    "/expire-pins",
    status_code=http_status.HTTP_200_OK,
    summary="Expire timed banner pins (cron)",
)
@limiter.limit("10/minute")
async def expire_notice_pins_internal(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
):
    """Deactivate banner pins past expiry."""
    count = await expire_notice_pins(db_connection)
    return success_response(
        request=request,
        message_key="notices.success.expire_pins_completed",
        custom_code=CustomStatusCode.SUCCESS,
        data={"expired_count": count},
    )
