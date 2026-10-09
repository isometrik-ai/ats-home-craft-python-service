"""Staff buy and sell API (organization-scoped, ADR 0019)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.marketplace import (
    AdminMarketplaceListQuery,
    AdminRemoveListingRequest,
    MarketplaceAdminListApiResponse,
    MarketplaceAdminListingApiResponse,
    MarketplaceAdminRemovedApiResponse,
    MarketplaceAdminSummaryApiResponse,
    MarketplaceCatalogApiResponse,
)
from apps.user_service.app.services.marketplace_service import MarketplaceService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access_optional,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.common_query import (
    MARKETPLACE_MANAGEMENT_EDIT,
    MARKETPLACE_MANAGEMENT_VIEW,
)
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/marketplace/admin", tags=["Marketplace (Admin)"])

OPTIONAL_PROJECT_ID = Query(
    None,
    description=(
        "Optional society filter (UUID). Omit to include every project in the organization."
    ),
)

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
            "model": model,
        },
    }


SUMMARY_SUCCESS_RESPONSES = _ok_response(
    MarketplaceAdminSummaryApiResponse,
    "Active, sold, past, and removed counts for the organization.",
)
CATALOG_SUCCESS_RESPONSES = _ok_response(
    MarketplaceCatalogApiResponse,
    "Category catalog for the staff filter.",
)
LIST_SUCCESS_RESPONSES = _ok_response(
    MarketplaceAdminListApiResponse,
    "Paginated marketplace listings for the organization.",
)
DETAIL_SUCCESS_RESPONSES = _ok_response(
    MarketplaceAdminListingApiResponse,
    "Listing detail for the staff drawer.",
)
REMOVED_SUCCESS_RESPONSES = _ok_response(
    MarketplaceAdminRemovedApiResponse,
    "Listing removed from the board.",
)


@handle_api_exceptions("get marketplace admin summary")
@router.get(
    "/summary",
    status_code=http_status.HTTP_200_OK,
    summary="Buy and sell summary for the organization",
    response_model=None,
    responses=SUMMARY_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_marketplace_admin_summary(
    request: Request,
    project_id: str | None = OPTIONAL_PROJECT_ID,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return active, sold, past, and removed counts. Filter with project_id."""
    user_context = await ensure_staff_project_access_optional(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=MARKETPLACE_MANAGEMENT_VIEW,
        request=request,
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.get_admin_summary(project_id=project_id)
    return success_response(
        request=request,
        message_key="marketplace.success.summary_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("get marketplace admin catalog")
@router.get(
    "/catalog",
    status_code=http_status.HTTP_200_OK,
    summary="Marketplace categories for staff filters",
    response_model=None,
    responses=CATALOG_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_marketplace_admin_catalog(
    request: Request,
    project_id: str | None = OPTIONAL_PROJECT_ID,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return static category options for the staff category filter."""
    user_context = await ensure_staff_project_access_optional(
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


@handle_api_exceptions("list marketplace admin listings")
@router.get(
    "/listings",
    status_code=http_status.HTTP_200_OK,
    summary="List marketplace listings for the organization",
    response_model=None,
    responses=LIST_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def list_marketplace_admin_listings(
    request: Request,
    query: AdminMarketplaceListQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Paginated listings with search, status, category, and optional project_id."""
    user_context = await ensure_staff_project_access_optional(
        current_user=current_user,
        db_connection=db_connection,
        project_id=query.project_id,
        permission_codes=MARKETPLACE_MANAGEMENT_VIEW,
        request=request,
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.list_listings_admin(query=query)
    return list_response(
        request=request,
        items=data["items"],
        total=data["total"],
        page=query.page,
        page_size=query.page_size,
        message_key="marketplace.success.listings_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("get marketplace admin listing")
@router.get(
    "/listings/{listing_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Get a marketplace listing for staff",
    response_model=None,
    responses=DETAIL_SUCCESS_RESPONSES,
)
@limiter.limit("100/minute")
async def get_marketplace_admin_listing(
    request: Request,
    listing_id: str = Path(..., description="Listing identifier (UUID string)."),
    project_id: str | None = OPTIONAL_PROJECT_ID,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return the staff drawer payload for one posted listing."""
    user_context = await ensure_staff_project_access_optional(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=MARKETPLACE_MANAGEMENT_VIEW,
        request=request,
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.get_listing_admin(listing_id=listing_id, project_id=project_id)
    return success_response(
        request=request,
        message_key="marketplace.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("remove marketplace admin listing")
@router.post(
    "/listings/{listing_id}/remove",
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
async def remove_marketplace_admin_listing(
    request: Request,
    listing_id: str = Path(..., description="Listing identifier (UUID string)."),
    project_id: str | None = OPTIONAL_PROJECT_ID,
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
    body: AdminRemoveListingRequest = Body(...),
):
    """Take a live listing off the board. Removal is permanent (no restore)."""
    user_context = await ensure_staff_project_access_optional(
        current_user=current_user,
        db_connection=db_connection,
        project_id=project_id,
        permission_codes=MARKETPLACE_MANAGEMENT_EDIT,
        request=request,
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.remove_listing_admin(
        listing_id=listing_id,
        removal_note=body.removal_note,
        project_id=project_id,
    )
    set_audit_context(
        request,
        user_context,
        project_id=project_id or data.get("project_id"),
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
