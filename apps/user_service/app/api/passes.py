"""Visitor passes API (resident-facing)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.passes import (
    CreatePassRequest,
    PassListQuery,
    UpdatePassRequest,
)
from apps.user_service.app.services.passes_service import PassesService
from apps.user_service.app.utils.common_utils import (
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/passes", tags=["Visitor Passes"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


def _service(*, db_connection: asyncpg.Connection, user_context) -> PassesService:
    """Build PassesService for the current request."""
    return PassesService(db_connection=db_connection, user_context=user_context)


@handle_api_exceptions("list visitor passes")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List my visitor passes",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_passes(
    request: Request,
    query: PassListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """List visitor passes for the authenticated resident."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(db_connection=db_connection, user_context=user_context)
    items, total = await service.list_passes(
        contact_id=str(contact["id"]),
        bucket=query.bucket.value if query.bucket else None,
        display_status=query.display_status.value if query.display_status else None,
        unit_id=query.unit_id,
        pass_type=query.pass_type.value if query.pass_type else None,
        page=query.page,
        page_size=query.page_size,
    )
    return list_response(
        request=request,
        items=items,
        total=total,
        page=query.page,
        page_size=query.page_size,
        message_key="passes.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("create visitor pass")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a visitor pass",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="passes",
    category="VISITOR_PASSES",
)
async def create_pass(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreatePassRequest = Body(...),
):
    """Create a visitor pass for a guest."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(db_connection=db_connection, user_context=user_context)
    data = await service.create_pass(contact_id=str(contact["id"]), body=body)
    return success_response(
        request=request,
        message_key="passes.success.pass_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data=data,
    )


@handle_api_exceptions("get visitor pass")
@router.get(
    "/{pass_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get visitor pass details",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_pass(
    request: Request,
    pass_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return pass details including timeline events."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(db_connection=db_connection, user_context=user_context)
    data = await service.get_pass(contact_id=str(contact["id"]), pass_id=pass_id)
    return success_response(
        request=request,
        message_key="passes.success.pass_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("update visitor pass")
@router.patch(
    "/{pass_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update a visitor pass",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="passes",
    category="VISITOR_PASSES",
)
async def update_pass(
    request: Request,
    pass_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: UpdatePassRequest = Body(...),
):
    """Update an upcoming or active visitor pass."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(db_connection=db_connection, user_context=user_context)
    data = await service.update_pass(
        contact_id=str(contact["id"]),
        pass_id=pass_id,
        body=body,
    )
    return success_response(
        request=request,
        message_key="passes.success.pass_updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("cancel visitor pass")
@router.post(
    "/{pass_id}/cancel",
    status_code=http_status.HTTP_200_OK,
    summary="Cancel a visitor pass",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["gdpr", "pii", "audit_required"],
    table_name="passes",
    category="VISITOR_PASSES",
)
async def cancel_pass(
    request: Request,
    pass_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Cancel an upcoming or active visitor pass."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(db_connection=db_connection, user_context=user_context)
    data = await service.cancel_pass(contact_id=str(contact["id"]), pass_id=pass_id)
    return success_response(
        request=request,
        message_key="passes.success.pass_cancelled",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("list visitor pass events")
@router.get(
    "/{pass_id}/events",
    status_code=http_status.HTTP_200_OK,
    summary="List pass timeline events",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_pass_events(
    request: Request,
    pass_id: str = Path(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the timeline for a visitor pass."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(db_connection=db_connection, user_context=user_context)
    items = await service.list_events(contact_id=str(contact["id"]), pass_id=pass_id)
    return list_response(
        request=request,
        items=items,
        total=len(items),
        message_key="passes.success.events_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )
