"""User push token API (authenticated user self-service)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_uow
from apps.user_service.app.schemas.user_push_tokens import (
    RegisterUserPushTokenRequest,
    UserPushTokenResponse,
)
from apps.user_service.app.services.user_push_token_service import UserPushTokenService
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/users/me/push-devices", tags=["User Push Tokens"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT or session)."},
    400: {"description": "Bad request."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("register my push device")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Register push device for authenticated user",
    description=(
        "Registers or refreshes a push device for the authenticated user. "
        "Reassigns the device row when another user logs in on the same device."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def register_my_push_device(
    request: Request,
    body: RegisterUserPushTokenRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Register or refresh push device for authenticated user."""
    service = await UserPushTokenService.for_end_user(
        db_connection=db_connection,
        current_user=current_user,
        request=request,
    )
    data = await service.register_device(body=body)
    response = UserPushTokenResponse.model_validate(data).model_dump(exclude_none=True)
    return success_response(
        request=request,
        message_key="users.success.push_device_registered",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=response,
    )


@handle_api_exceptions("unregister my push device")
@router.delete(
    "/{device_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Unregister push device for authenticated user",
    description=(
        "Removes a push device registration for the authenticated user. "
        "Idempotent when the device is already unregistered."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def unregister_my_push_device(
    request: Request,
    device_id: str = Path(..., description="Client-stable device identifier."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Unregister push device for authenticated user."""
    service = await UserPushTokenService.for_end_user(
        db_connection=db_connection,
        current_user=current_user,
        request=request,
    )
    data = await service.unregister_device(device_id=device_id)
    return success_response(
        request=request,
        message_key="users.success.push_device_unregistered",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=data,
    )
