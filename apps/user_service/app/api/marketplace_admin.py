"""Staff buy and sell API (project-scoped, ADR 0019)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.marketplace import (
    AdminMarketplaceListQuery,
    AdminRemoveListingRequest,
    MarketplaceCatalogApiResponse,
    MarketplaceListApiResponse,
    MarketplaceListingApiResponse,
    MarketplaceSummaryApiResponse,
)
from apps.user_service.app.services.marketplace_service import MarketplaceService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    MARKETPLACE_MANAGEMENT_EDIT,
    MARKETPLACE_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/projects", tags=["Marketplace (Admin)"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden (insufficient permissions)."},
    404: {"description": "Not found."},
    409: {"description": "Conflict."},
    422: {"description": "Validation error."},
    429: {"description": "Too many requests (rate limited)."},
    500: {"description": "Internal server error."},
}


def _ok_response(
    model: type,
    description: str,
    *,
    status_code: int = http_status.HTTP_200_OK,
) -> dict[int | str, dict]:
    """Build OpenAPI responses for a successful JSON envelope."""
    return {
        **COMMON_ERROR_RESPONSES,
        status_code: {
            "description": description,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        },
    }


SUMMARY_SUCCESS_RESPONSES = _ok_response(
    MarketplaceSummaryApiResponse,
    "Active, sold, past, and removed counts for the project.",
)
CATALOG_SUCCESS_RESPONSES = _ok_response(
    MarketplaceCatalogApiResponse,
    "Category catalog for the staff filter.",
)
LIST_SUCCESS_RESPONSES = _ok_response(
    MarketplaceListApiResponse,
    "Paginated marketplace listings for the project.",
)
DETAIL_SUCCESS_RESPONSES = _ok_response(
    MarketplaceListingApiResponse,
    "Listing detail for the staff drawer.",
)
REMOVED_SUCCESS_RESPONSES = _ok_response(
    MarketplaceListingApiResponse,
    "Listing removed from the board.",
)


@handle_api_exceptions("get project marketplace summary")
@router.get(
    "/{project_id}/marketplace/summary",
    status_code=http_status.HTTP_200_OK,
    summary="Buy and sell summary for a project",
    response_model=None,
    responses=SUMMARY_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_marketplace_summary(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return active, sold, past, and removed counts for the listings header."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=MARKETPLACE_MANAGEMENT_VIEW,
        request=request,
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.get_project_summary(project_id=project_id)
    return success_response(
        request=request,
        message_key="marketplace.success.summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("get project marketplace catalog")
@router.get(
    "/{project_id}/marketplace/catalog",
    status_code=http_status.HTTP_200_OK,
    summary="Marketplace categories for staff filters",
    response_model=None,
    responses=CATALOG_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_marketplace_catalog(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return static category options for the staff category filter."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=MARKETPLACE_MANAGEMENT_VIEW,
        request=request,
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.get_catalog()
    return success_response(
        request=request,
        message_key="marketplace.success.catalog_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("list project marketplace listings")
@router.get(
    "/{project_id}/marketplace/listings",
    status_code=http_status.HTTP_200_OK,
    summary="List marketplace listings for a project",
    response_model=None,
    responses=LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_project_marketplace_listings(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    query: AdminMarketplaceListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Paginated listings with search, status, and category. No tower grouping."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=MARKETPLACE_MANAGEMENT_VIEW,
        request=request,
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.list_listings_for_project(project_id=project_id, query=query)
    return list_response(
        request=request,
        items=data["items"],
        total=data["total"],
        page=query.page,
        page_size=query.page_size,
        message_key="marketplace.success.listings_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get project marketplace listing")
@router.get(
    "/{project_id}/marketplace/listings/{listing_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get a marketplace listing for staff",
    response_model=None,
    responses=DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_project_marketplace_listing(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    listing_id: str = Path(..., description="Listing identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the staff drawer payload for one posted listing."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=MARKETPLACE_MANAGEMENT_VIEW,
        request=request,
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.get_listing_for_project(project_id=project_id, listing_id=listing_id)
    return success_response(
        request=request,
        message_key="marketplace.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("remove project marketplace listing")
@router.post(
    "/{project_id}/marketplace/listings/{listing_id}/remove",
    status_code=http_status.HTTP_200_OK,
    summary="Remove a live marketplace listing",
    response_model=None,
    responses=REMOVED_SUCCESS_RESPONSES,
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="marketplace_listings",
    category="MARKETPLACE",
)
async def remove_project_marketplace_listing(
    request: Request,
    project_id: str = Path(..., description="Project identifier (UUID string)."),
    listing_id: str = Path(..., description="Listing identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: AdminRemoveListingRequest = Body(...),
):
    """Take a live listing off the board. Removal is permanent (no restore)."""
    user_context = await ensure_staff_project_access(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=MARKETPLACE_MANAGEMENT_EDIT,
        request=request,
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.remove_listing_admin(
        project_id=project_id,
        listing_id=listing_id,
        removal_note=body.removal_note,
    )
    set_audit_context(
        request,
        user_context,
        project_id=project_id,
        table="marketplace_listings",
        requested_id=listing_id,
        description=f"Staff removed marketplace listing: {listing_id}",
        risk_level="low",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="marketplace.success.removed",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
