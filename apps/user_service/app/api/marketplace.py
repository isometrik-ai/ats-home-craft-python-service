"""Resident buy and sell API (ADR 0019)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.db import db_conn, db_uow
from apps.user_service.app.schemas.enums.marketplace import MarketplaceMineStatus
from apps.user_service.app.schemas.marketplace import (
    BrowseListingsQuery,
    CreateListingRequest,
    MarketplaceCatalogApiResponse,
    MarketplaceListApiResponse,
    MarketplaceListingApiResponse,
    MarkSoldRequest,
    PublishListingRequest,
    RemoveListingRequest,
    SaveListingRequest,
    UpdateListingRequest,
)
from apps.user_service.app.services.marketplace_service import MarketplaceService
from apps.user_service.app.utils.audit_context import set_audit_context
from apps.user_service.app.utils.common_utils import (
    extract_onboarding_contact_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/marketplace", tags=["Marketplace (Resident)"])

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized (missing/invalid JWT)."},
    403: {"description": "Forbidden."},
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


def _created_response(model: type, description: str) -> dict[int | str, dict]:
    """Build OpenAPI responses for HTTP 201 success."""
    return _ok_response(model, description, status_code=http_status.HTTP_201_CREATED)


@handle_api_exceptions("get marketplace catalog")
@router.get(
    "/catalog",
    status_code=http_status.HTTP_200_OK,
    summary="Marketplace categories",
    response_model=None,
    responses=_ok_response(MarketplaceCatalogApiResponse, "Category catalog retrieved."),
)
@limiter.limit("100/minute")
async def marketplace_catalog(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return static category and subtype options."""
    user_context, _ = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.get_catalog()
    return success_response(
        request=request,
        message_key="marketplace.success.catalog_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("list marketplace listings")
@router.get(
    "/listings",
    status_code=http_status.HTTP_200_OK,
    summary="Browse live listings",
    response_model=None,
    responses=_ok_response(MarketplaceListApiResponse, "Listing cards retrieved."),
)
@limiter.limit("100/minute")
async def list_marketplace_listings(
    request: Request,
    filters: BrowseListingsQuery = Depends(),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Paginated live listings for the organization."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.list_listings(
        contact_id=str(contact["id"]),
        query=filters,
    )
    return list_response(
        request=request,
        items=data["items"],
        total=data["total"],
        page=filters.page,
        page_size=filters.page_size,
        message_key="marketplace.success.listings_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("list saved listings")
@router.get(
    "/saved",
    status_code=http_status.HTTP_200_OK,
    summary="Saved listings",
    response_model=None,
    responses=_ok_response(MarketplaceListApiResponse, "Saved listing cards retrieved."),
)
@limiter.limit("100/minute")
async def list_saved_listings(
    request: Request,
    page: int = Query(default=1, ge=1, le=21_474_836),
    page_size: int = Query(default=20, ge=1, le=100),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Paginated bookmarks that are still live."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.list_saved(
        contact_id=str(contact["id"]),
        page=page,
        page_size=page_size,
    )
    return list_response(
        request=request,
        items=data["items"],
        total=data["total"],
        page=page,
        page_size=page_size,
        message_key="marketplace.success.saved_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("list my listings")
@router.get(
    "/me/listings",
    status_code=http_status.HTTP_200_OK,
    summary="My listings",
    response_model=None,
    responses=_ok_response(MarketplaceListApiResponse, "Seller dashboard list retrieved."),
)
@limiter.limit("100/minute")
async def my_listings(
    request: Request,
    status: MarketplaceMineStatus = Query(default=MarketplaceMineStatus.ALL),
    page: int = Query(default=1, ge=1, le=21_474_836),
    page_size: int = Query(default=20, ge=1, le=100),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Paginated listings owned by the signed-in contact."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.my_listings(
        contact_id=str(contact["id"]),
        status=status.value,
        page=page,
        page_size=page_size,
    )
    return list_response(
        request=request,
        items=data["items"],
        total=data["total"],
        page=page,
        page_size=page_size,
        message_key="marketplace.success.mine_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("create marketplace listing")
@router.post(
    "/listings",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a listing",
    response_model=None,
    responses=_created_response(MarketplaceListingApiResponse, "Listing created."),
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="CREATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="marketplace_listings",
    category="MARKETPLACE",
)
async def create_listing(
    request: Request,
    body: CreateListingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create a listing. It stays unpublished until POST /listings/{id}/publish."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.create_listing(contact_id=str(contact["id"]), body=body)
    set_audit_context(
        request,
        user_context,
        table="marketplace_listings",
        requested_id=str(data.get("id")),
        description=f"Created marketplace listing for unit: {body.unit_id}",
        risk_level="low",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="marketplace.success.created",
        status_code=http_status.HTTP_201_CREATED,
        custom_code=CustomStatusCode.CREATED,
        data=data,
    )


@handle_api_exceptions("get marketplace listing")
@router.get(
    "/listings/{listing_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Listing detail",
    response_model=None,
    responses=_ok_response(MarketplaceListingApiResponse, "Listing detail retrieved."),
)
@limiter.limit("100/minute")
async def get_listing(
    request: Request,
    listing_id: str = Path(..., description="Listing identifier (UUID string)."),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Return one listing."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.get_listing(contact_id=str(contact["id"]), listing_id=listing_id)
    return success_response(
        request=request,
        message_key="marketplace.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("update marketplace listing")
@router.patch(
    "/listings/{listing_id}",
    status_code=http_status.HTTP_200_OK,
    summary="Edit a listing",
    response_model=None,
    responses=_ok_response(MarketplaceListingApiResponse, "Listing updated."),
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="marketplace_listings",
    category="MARKETPLACE",
)
async def update_listing(
    request: Request,
    listing_id: str = Path(..., description="Listing identifier (UUID string)."),
    body: UpdateListingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Patch listing fields while draft or live (seller only).

    Status is not editable here. Live rows stay live if still valid.
    Media cannot change after create.
    """
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.update_listing(
        contact_id=str(contact["id"]),
        listing_id=listing_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="marketplace_listings",
        requested_id=listing_id,
        description=f"Updated marketplace listing: {listing_id}",
        risk_level="low",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="marketplace.success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("publish marketplace listing")
@router.post(
    "/listings/{listing_id}/publish",
    status_code=http_status.HTTP_200_OK,
    summary="Post listing (go live)",
    response_model=None,
    responses=_ok_response(MarketplaceListingApiResponse, "Listing is live."),
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="marketplace_listings",
    category="MARKETPLACE",
)
async def publish_listing(
    request: Request,
    listing_id: str = Path(..., description="Listing identifier (UUID string)."),
    body: PublishListingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Go live. Only unpublished listings can be published."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.publish(
        contact_id=str(contact["id"]),
        listing_id=listing_id,
        body=body,
    )
    set_audit_context(
        request,
        user_context,
        table="marketplace_listings",
        requested_id=listing_id,
        description=f"Published marketplace listing: {listing_id}",
        risk_level="low",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="marketplace.success.published",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("remove marketplace listing")
@router.post(
    "/listings/{listing_id}/remove",
    status_code=http_status.HTTP_200_OK,
    summary="Delete a draft listing or remove a live listing",
    response_model=None,
    responses=_ok_response(MarketplaceListingApiResponse, "Draft deleted or live listing removed."),
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="DELETE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="marketplace_listings",
    category="MARKETPLACE",
)
async def remove_listing(
    request: Request,
    listing_id: str = Path(..., description="Listing identifier (UUID string)."),
    body: RemoveListingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Draft: hard-delete. Live: soft-remove; seller must create a new listing to post again."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.remove_listing(
        contact_id=str(contact["id"]),
        listing_id=listing_id,
        unit_id=body.unit_id,
        removal_note=body.removal_note,
    )
    deleted = data.get("status") == "deleted"
    set_audit_context(
        request,
        user_context,
        table="marketplace_listings",
        requested_id=listing_id,
        description=(
            f"Deleted draft marketplace listing: {listing_id}"
            if deleted
            else f"Removed marketplace listing: {listing_id}"
        ),
        risk_level="low",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key=("marketplace.success.deleted" if deleted else "marketplace.success.removed"),
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("mark listing sold")
@router.post(
    "/listings/{listing_id}/mark-sold",
    status_code=http_status.HTTP_200_OK,
    summary="Mark a listing sold",
    response_model=None,
    responses=_ok_response(MarketplaceListingApiResponse, "Listing marked sold."),
)
@limiter.limit("30/minute")
@audit_api_call(
    action_type="UPDATE",
    data_classification="pii",
    compliance_tags=["audit_required"],
    table_name="marketplace_listings",
    category="MARKETPLACE",
)
async def mark_listing_sold(
    request: Request,
    listing_id: str = Path(..., description="Listing identifier (UUID string)."),
    body: MarkSoldRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_uow),
    current_user: dict = Depends(get_user_from_auth),
):
    """Record the buyer and an optional private rating."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    data = await service.mark_sold(contact_id=str(contact["id"]), listing_id=listing_id, body=body)
    set_audit_context(
        request,
        user_context,
        table="marketplace_listings",
        requested_id=listing_id,
        description=f"Marked marketplace listing sold: {listing_id}",
        risk_level="low",
        new_data=data,
    )
    return success_response(
        request=request,
        message_key="marketplace.success.sold",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("save marketplace listing")
@router.post(
    "/listings/{listing_id}/save",
    status_code=http_status.HTTP_200_OK,
    summary="Save or unsave a listing",
    response_model=None,
    responses=_ok_response(MarketplaceListingApiResponse, "Bookmark updated."),
)
@limiter.limit("60/minute")
async def set_listing_saved(
    request: Request,
    listing_id: str = Path(..., description="Listing identifier (UUID string)."),
    body: SaveListingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Bookmark a live listing, or clear that bookmark."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = MarketplaceService(db_connection=db_connection, user_context=user_context)
    contact_id = str(contact["id"])
    if body.saved:
        await service.save(contact_id=contact_id, listing_id=listing_id, unit_id=body.unit_id)
        message_key = "marketplace.success.saved"
    else:
        await service.unsave(contact_id=contact_id, listing_id=listing_id, unit_id=body.unit_id)
        message_key = "marketplace.success.unsaved"
    return success_response(
        request=request,
        message_key=message_key,
        custom_code=CustomStatusCode.SUCCESS,
    )
