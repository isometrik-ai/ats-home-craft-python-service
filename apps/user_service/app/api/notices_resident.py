"""Resident notice board API."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.notices import (
    ResidentBannerQuery,
    ResidentNoticeListQuery,
)
from apps.user_service.app.services.notices_resident_service import (
    NoticesResidentService,
)
from apps.user_service.app.utils.common_utils import (
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/notices", tags=["Notices (Resident)"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("get resident notice banner")
@router.get(
    "/banner",
    status_code=http_status.HTTP_200_OK,
    summary="Pinned notices visible to resident",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def get_resident_notice_banner(
    request: Request,
    query: ResidentBannerQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return up to six pinned live notices visible to the caller."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = NoticesResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items = await service.get_banner(
        contact_id=str(contact["id"]),
        contact_user_id=str(contact["user_id"]) if contact.get("user_id") else None,
        query=query,
    )
    return list_response(
        request=request,
        items=[item.model_dump(mode="json") for item in items],
        total=len(items),
        page=1,
        page_size=max(len(items), 1),
        message_key="notices.success.banner_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
    )


@handle_api_exceptions("list resident notices")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="Notice feed for resident",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def list_resident_notices(
    request: Request,
    query: ResidentNoticeListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return paginated live notices visible to the caller."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = NoticesResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    items, total = await service.list_notices(
        contact_id=str(contact["id"]),
        contact_user_id=str(contact["user_id"]) if contact.get("user_id") else None,
        query=query,
    )
    return list_response(
        request=request,
        items=[item.model_dump(mode="json") for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="notices.success.feed_retrieved",
        custom_code=CustomStatusCode.SUCCESS if items else CustomStatusCode.NO_CONTENT,
    )


@handle_api_exceptions("get resident notice detail")
@router.get(
    "/{notice_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Notice detail for resident",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def get_resident_notice(
    request: Request,
    notice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return notice detail and increment view count."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = NoticesResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.get_notice(
        contact_id=str(contact["id"]),
        contact_user_id=str(contact["user_id"]) if contact.get("user_id") else None,
        notice_id=notice_id,
    )
    return success_response(
        request=request,
        message_key="notices.success.retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("like resident notice")
@router.post(
    "/{notice_id}/like",
    status_code=http_status.HTTP_200_OK,
    summary="Like a notice",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def like_resident_notice(
    request: Request,
    notice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Like a visible notice."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = NoticesResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.like_notice(
        contact_id=str(contact["id"]),
        contact_user_id=str(contact["user_id"]) if contact.get("user_id") else None,
        notice_id=notice_id,
    )
    return success_response(
        request=request,
        message_key="notices.success.liked",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )


@handle_api_exceptions("unlike resident notice")
@router.delete(
    "/{notice_id}/like",
    status_code=http_status.HTTP_200_OK,
    summary="Remove like from a notice",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def unlike_resident_notice(
    request: Request,
    notice_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Remove like from a visible notice."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = NoticesResidentService(
        db_connection=db_connection,
        user_context=user_context,
    )
    data = await service.unlike_notice(
        contact_id=str(contact["id"]),
        contact_user_id=str(contact["user_id"]) if contact.get("user_id") else None,
        notice_id=notice_id,
    )
    return success_response(
        request=request,
        message_key="notices.success.unliked",
        custom_code=CustomStatusCode.SUCCESS,
        data=data.model_dump(mode="json"),
    )
