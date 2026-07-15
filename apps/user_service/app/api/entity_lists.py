"""Entity lists API.

Provides organization-scoped list management and bulk membership operations for:
- contacts
- companies
- leads
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.entity_lists import (
    CreateEntityListRequest,
    EntityListDetails,
    EntityListSummary,
    UpdateEntityListRequest,
)
from apps.user_service.app.schemas.enums import EntityListStatus, EntityType
from apps.user_service.app.services.entity_lists_service import EntityListsService
from apps.user_service.app.utils.common_utils import (
    check_permissions,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/lists", tags=["Lists"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


@handle_api_exceptions("create list")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a list",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def create_list(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: CreateEntityListRequest = Body(...),
):
    """Create a list for contacts/companies/leads."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=EntityListsService.get_permission_code(
            entity_type=body.entity_type,
            action="create",
        ),
    )
    service = EntityListsService(
        db_connection=db_connection,
        organization_id=user_context.organization_id,
    )
    created_payload = await service.create_list(body)
    created_list = created_payload.get("list") or {}
    request.state.audit_requested_id = str(created_list.get("id"))
    request.state.audit_description = f"Created list: {created_list.get('id')}"

    return success_response(
        request=request,
        message_key="entity_lists.success.list_created",
        custom_code=CustomStatusCode.CREATED,
        status_code=http_status.HTTP_201_CREATED,
        data={
            "id": str(created_list.get("id")),
            "members": created_payload.get("members"),
        },
    )


@handle_api_exceptions("list lists")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List lists",
    response_model=None,
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def list_lists(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    entity_type: EntityType = Query(..., description="Filter by entity type"),
    status: EntityListStatus | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List lists for an entity type with derived counters."""
    user_context = await check_permissions(
        current_user=current_user,
        db_connection=db_connection,
        permission_codes=EntityListsService.get_permission_code(
            entity_type=entity_type,
            action="view",
        ),
    )
    service = EntityListsService(
        db_connection=db_connection,
        organization_id=user_context.organization_id,
    )
    offset = (page - 1) * page_size
    items, total = await service.list_lists(
        entity_type=entity_type,
        status=status,
        search=search,
        limit=page_size,
        offset=offset,
    )
    summaries = [EntityListSummary.model_validate(i).model_dump(mode="json") for i in items]
    if not summaries:
        return list_response(
            request=request,
            items=[],
            total=0,
            message_key="entity_lists.success.no_lists_found",
            custom_code=CustomStatusCode.NO_CONTENT,
            status_code=http_status.HTTP_200_OK,
            page=page,
            page_size=page_size,
        )
    return list_response(
        request=request,
        items=summaries,
        total=total,
        message_key="entity_lists.success.lists_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        page=page,
        page_size=page_size,
    )


@handle_api_exceptions("get list details")
@router.get(
    "/{list_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get list details",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def get_list_details(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    list_id: str = Path(...),
):
    """Return list details by list id."""
    user_context, _ = await EntityListsService.require_list_permission(
        current_user=current_user,
        db_connection=db_connection,
        list_id=list_id,
        action="view",
    )
    service = EntityListsService(
        db_connection=db_connection,
        organization_id=user_context.organization_id,
    )
    details = await service.get_list_details(list_id=list_id)
    return success_response(
        request=request,
        message_key="entity_lists.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=EntityListDetails.model_validate(details).model_dump(mode="json"),
    )


@handle_api_exceptions("update list")
@router.patch(
    "/{list_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update list",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def update_list(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    list_id: str = Path(...),
    body: UpdateEntityListRequest = Body(...),
):
    """Update list metadata, status, and membership."""
    user_context, _ = await EntityListsService.require_list_permission(
        current_user=current_user,
        db_connection=db_connection,
        list_id=list_id,
        action="edit",
    )
    service = EntityListsService(
        db_connection=db_connection,
        organization_id=user_context.organization_id,
    )
    updated = await service.update_list(list_id=list_id, body=body)
    request.state.audit_requested_id = str(updated.get("id"))
    request.state.audit_description = f"Updated list: {updated.get('id')}"
    return success_response(
        request=request,
        message_key="entity_lists.success.list_updated",
        custom_code=CustomStatusCode.UPDATED,
        status_code=http_status.HTTP_200_OK,
    )


@handle_api_exceptions("delete list")
@router.delete(
    "/{list_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete list",
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def delete_list(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
    list_id: str = Path(...),
):
    """Soft delete a list."""
    user_context, _ = await EntityListsService.require_list_permission(
        current_user=current_user,
        db_connection=db_connection,
        list_id=list_id,
        action="delete",
    )
    service = EntityListsService(
        db_connection=db_connection,
        organization_id=user_context.organization_id,
    )
    await service.soft_delete(list_id=list_id)
    request.state.audit_requested_id = str(list_id)
    request.state.audit_description = f"Deleted list: {list_id}"
    return success_response(
        request=request,
        message_key="entity_lists.success.list_deleted",
        custom_code=CustomStatusCode.DELETED,
        status_code=http_status.HTTP_200_OK,
    )
