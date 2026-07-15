"""External Entity Lists API.

These endpoints are intended for external integrations (partners, embedded apps)
that do not authenticate with our JWT bearer token. Instead, the caller
authenticates via Isometrik credential decode using headers:

- ``licenseKey``
- ``appSecret``

The decoded ``projectId`` is mapped to our internal ``organization_id`` and all
operations are scoped to that organization.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.dependencies.external_auth import get_organization_context
from apps.user_service.app.schemas.entity_lists import (
    CreateEntityListRequest,
    EntityListDetails,
    EntityListSummary,
    UpdateEntityListRequest,
)
from apps.user_service.app.schemas.enums import EntityListStatus, EntityType
from apps.user_service.app.services.entity_lists_service import EntityListsService
from apps.user_service.app.utils.common_utils import handle_api_exceptions
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/integrations/lists", tags=["Lists (External)"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    http_status.HTTP_400_BAD_REQUEST: {"description": "Bad request"},
    http_status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    http_status.HTTP_404_NOT_FOUND: {"description": "Not found"},
    http_status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Validation error"},
    http_status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Too many requests"},
    http_status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    http_status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service unavailable"},
}


@handle_api_exceptions("external create list")
@router.post(
    "",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a list (external auth)",
    description=(
        "Create a list for contacts/companies/leads. Organization is resolved from "
        "Isometrik credentials (`licenseKey`/`appSecret`)."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def external_create_list(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_uow),
    organization_id: str = Depends(get_organization_context),
    body: CreateEntityListRequest = Body(...),
):
    """External create list endpoint (Isometrik credential auth)."""
    service = EntityListsService(
        db_connection=db_connection,
        organization_id=organization_id,
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


@handle_api_exceptions("external list lists")
@router.get(
    "",
    status_code=http_status.HTTP_200_OK,
    summary="List lists (external auth)",
    description=(
        "List lists for an entity type with derived counters. Organization is resolved "
        "from Isometrik credentials (`licenseKey`/`appSecret`)."
    ),
    response_model=None,
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("200/minute")
async def external_list_lists(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    entity_type: EntityType = Query(..., description="Filter by entity type"),
    status: EntityListStatus | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """External list lists endpoint (Isometrik credential auth)."""
    service = EntityListsService(
        db_connection=db_connection,
        organization_id=organization_id,
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


@handle_api_exceptions("external get list details")
@router.get(
    "/{list_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get list details (external auth)",
    description=(
        "Return list details by list id. Organization is resolved from Isometrik "
        "credentials (`licenseKey`/`appSecret`)."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("200/minute")
async def external_get_list_details(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    list_id: str = Path(...),
):
    """External get list details endpoint (Isometrik credential auth)."""
    service = EntityListsService(
        db_connection=db_connection,
        organization_id=organization_id,
    )
    details = await service.get_list_details(list_id=list_id)
    return success_response(
        request=request,
        message_key="entity_lists.success.list_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        status_code=http_status.HTTP_200_OK,
        data=EntityListDetails.model_validate(details).model_dump(mode="json"),
    )


@handle_api_exceptions("external update list")
@router.patch(
    "/{list_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Update list (external auth)",
    description=(
        "Update list metadata, status, and membership. Organization is resolved from "
        "Isometrik credentials (`licenseKey`/`appSecret`)."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("100/minute")
async def external_update_list(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    list_id: str = Path(...),
    body: UpdateEntityListRequest = Body(...),
):
    """External update list endpoint (Isometrik credential auth)."""
    service = EntityListsService(
        db_connection=db_connection,
        organization_id=organization_id,
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


@handle_api_exceptions("external delete list")
@router.delete(
    "/{list_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Delete list (external auth)",
    description=(
        "Soft delete a list. Organization is resolved from Isometrik credentials "
        "(`licenseKey`/`appSecret`)."
    ),
    responses=COMMON_ERROR_RESPONSES,
)
@limiter.limit("60/minute")
async def external_delete_list(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    organization_id: str = Depends(get_organization_context),
    list_id: str = Path(...),
):
    """External delete list endpoint (Isometrik credential auth)."""
    service = EntityListsService(
        db_connection=db_connection,
        organization_id=organization_id,
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
