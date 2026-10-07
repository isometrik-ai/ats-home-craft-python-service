"""Resident and committee buy and sell API (ADR 0019)."""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Body, Depends, Path, Query, Request
from fastapi import status as http_status

from apps.user_service.app.app_instance import limiter
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.schemas.enums.marketplace import (
    MarketplaceItemCondition,
    MarketplaceListingAction,
    MarketplaceMineStatus,
    MarketplacePriceBand,
    MarketplaceReportDecision,
    MarketplaceSort,
    MarketplaceWhere,
)
from apps.user_service.app.schemas.marketplace import (
    AddListingMediaRequest,
    CreateListingRequest,
    CreateReportRequest,
    ListingActionRequest,
    MarkSoldRequest,
    PublishListingRequest,
    ReviewReportRequest,
    SaveListingRequest,
    UpdateListingRequest,
)
from apps.user_service.app.services.marketplace_service import MarketplaceService
from apps.user_service.app.utils.common_utils import (
    ensure_staff_project_access_for_context,
    extract_onboarding_contact_context,
    extract_user_context,
    handle_api_exceptions,
)
from libs.shared_middleware.jwt_auth import get_user_from_auth
from libs.shared_utils.http_exceptions import ForbiddenException, ValidationException
from libs.shared_utils.response_factory import list_response, success_response
from libs.shared_utils.status_codes import CustomStatusCode

router = APIRouter(prefix="/marketplace", tags=["Marketplace"])

_VIEW = "marketplace_management.view"
_MODERATE = "marketplace_management.moderate"


def _service(db_connection: asyncpg.Connection, user_context) -> MarketplaceService:
    """Build the marketplace service for this request."""
    return MarketplaceService(db_connection=db_connection, user_context=user_context)


@handle_api_exceptions("get marketplace home")
@router.get("/home", summary="Buy and sell home")
@limiter.limit("100/minute")
async def marketplace_home(
    request: Request,
    unit_id: str = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Society header, the latest draft, and recently listed posts."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    data = await _service(db_connection, user_context).home(
        contact_id=str(contact["id"]), unit_id=unit_id
    )
    return success_response(
        request=request,
        message_key="marketplace.success.home_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("get marketplace catalog")
@router.get("/catalog", summary="Marketplace categories")
@limiter.limit("100/minute")
async def marketplace_catalog(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Category and subtype picker."""
    user_context, _ = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    data = await _service(db_connection, user_context).get_catalog()
    return success_response(
        request=request,
        message_key="marketplace.success.catalog_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("list marketplace pickup units")
@router.get("/pickup-units", summary="Pickup flats")
@limiter.limit("100/minute")
async def marketplace_pickup_units(
    request: Request,
    unit_id: str = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Flats the caller may collect from."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    data = await _service(db_connection, user_context).pickup_units(
        contact_id=str(contact["id"]), unit_id=unit_id
    )
    return success_response(
        request=request,
        message_key="marketplace.success.pickup_units_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("list marketplace listings")
@router.get("/listings", summary="Browse listings")
@limiter.limit("100/minute")
async def list_marketplace_listings(
    request: Request,
    *,
    unit_id: str = Query(...),
    category: str | None = Query(default=None),
    subtype: str | None = Query(default=None),
    q: str | None = Query(default=None),
    sort: MarketplaceSort = Query(default=MarketplaceSort.NEWEST),
    price_band: MarketplacePriceBand | None = Query(default=None),
    condition: list[MarketplaceItemCondition] | None = Query(default=None),
    where: MarketplaceWhere | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Live listings in this society and nearby societies."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    data = await _service(db_connection, user_context).list_listings(
        contact_id=str(contact["id"]),
        unit_id=unit_id,
        category=category,
        subtype=subtype,
        query=q,
        sort=sort.value,
        price_band=price_band.value if price_band else None,
        conditions=[item.value for item in condition] if condition else [],
        where=where.value if where else None,
        page=page,
        page_size=page_size,
    )
    return list_response(
        request=request,
        items=data["items"],
        total=data["total"],
        page=page,
        page_size=page_size,
        message_key="marketplace.success.listings_retrieved",
    )


@handle_api_exceptions("list saved listings")
@router.get("/saved", summary="Saved listings")
@limiter.limit("100/minute")
async def list_saved_listings(
    request: Request,
    unit_id: str = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Bookmarks that are still live."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    items = await _service(db_connection, user_context).list_saved(
        contact_id=str(contact["id"]), unit_id=unit_id
    )
    return success_response(
        request=request,
        message_key="marketplace.success.saved_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=items,
    )


@handle_api_exceptions("list my listings")
@router.get("/me/listings", summary="My listings")
@limiter.limit("100/minute")
async def my_listings(
    request: Request,
    unit_id: str = Query(...),
    status: MarketplaceMineStatus = Query(default=MarketplaceMineStatus.ALL),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """The caller's drafts, live posts, sold posts, and past posts."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    data = await _service(db_connection, user_context).my_listings(
        contact_id=str(contact["id"]), unit_id=unit_id, status=status.value
    )
    return success_response(
        request=request,
        message_key="marketplace.success.mine_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("create marketplace listing")
@router.post("/listings", status_code=http_status.HTTP_201_CREATED, summary="Start a draft")
@limiter.limit("30/minute")
async def create_listing(
    request: Request,
    body: CreateListingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Create a draft in the chosen category."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    data = await _service(db_connection, user_context).create_listing(
        contact_id=str(contact["id"]), body=body
    )
    return success_response(
        request=request,
        message_key="marketplace.success.draft_created",
        status_code=http_status.HTTP_201_CREATED,
        custom_code=CustomStatusCode.CREATED,
        data=data,
    )


@handle_api_exceptions("get marketplace listing")
@router.get("/listings/{listing_id}", summary="Listing detail")
@limiter.limit("100/minute")
async def get_listing(
    request: Request,
    listing_id: str = Path(...),
    unit_id: str = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """What neighbours see, plus seller-only fields when the caller owns it."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    data = await _service(db_connection, user_context).get_listing(
        contact_id=str(contact["id"]), listing_id=listing_id, unit_id=unit_id
    )
    return success_response(
        request=request,
        message_key="marketplace.success.detail_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("update marketplace listing")
@router.patch("/listings/{listing_id}", summary="Edit a listing")
@limiter.limit("30/minute")
async def update_listing(
    request: Request,
    listing_id: str = Path(...),
    body: UpdateListingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Edit a draft, a live post, or a post the committee removed."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    data = await _service(db_connection, user_context).update_listing(
        contact_id=str(contact["id"]), listing_id=listing_id, body=body
    )
    return success_response(
        request=request,
        message_key="marketplace.success.updated",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


_ACTION_MESSAGES = {
    MarketplaceListingAction.PUBLISH: "marketplace.success.published",
    MarketplaceListingAction.REMOVE: "marketplace.success.removed",
    MarketplaceListingAction.RESTORE: "marketplace.success.restored",
    MarketplaceListingAction.RENEW: "marketplace.success.renewed",
    MarketplaceListingAction.RELIST: "marketplace.success.relisted",
}


@handle_api_exceptions("marketplace listing action")
@router.post("/listings/{listing_id}/actions", summary="Publish, remove, restore, renew, or relist")
@limiter.limit("30/minute")
async def listing_action(
    request: Request,
    listing_id: str = Path(...),
    body: ListingActionRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """One route for the seller status changes. action picks the change."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(db_connection, user_context)
    contact_id = str(contact["id"])
    if body.action == MarketplaceListingAction.PUBLISH:
        data = await service.publish(
            contact_id=contact_id,
            listing_id=listing_id,
            body=PublishListingRequest(
                unit_id=body.unit_id,
                rules_accepted=bool(body.rules_accepted),
            ),
        )
    elif body.action == MarketplaceListingAction.REMOVE:
        data = await service.remove_listing(
            contact_id=contact_id, listing_id=listing_id, unit_id=body.unit_id
        )
    elif body.action == MarketplaceListingAction.RESTORE:
        data = await service.restore_listing(
            contact_id=contact_id, listing_id=listing_id, unit_id=body.unit_id
        )
    elif body.action == MarketplaceListingAction.RENEW:
        data = await service.renew(
            contact_id=contact_id, listing_id=listing_id, unit_id=body.unit_id
        )
    else:
        data = await service.relist(
            contact_id=contact_id, listing_id=listing_id, unit_id=body.unit_id
        )
    return success_response(
        request=request,
        message_key=_ACTION_MESSAGES[body.action],
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("add listing media")
@router.post("/listings/{listing_id}/media", status_code=http_status.HTTP_201_CREATED)
@limiter.limit("30/minute")
async def add_listing_media(
    request: Request,
    listing_id: str = Path(...),
    body: AddListingMediaRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Record a file uploaded with a presigned URL."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    data = await _service(db_connection, user_context).add_media(
        contact_id=str(contact["id"]), listing_id=listing_id, body=body
    )
    return success_response(
        request=request,
        message_key="marketplace.success.media_added",
        status_code=http_status.HTTP_201_CREATED,
        custom_code=CustomStatusCode.CREATED,
        data=data,
    )


@handle_api_exceptions("delete listing media")
@router.delete("/listings/{listing_id}/media/{media_id}", status_code=http_status.HTTP_200_OK)
@limiter.limit("30/minute")
async def delete_listing_media(
    request: Request,
    listing_id: str = Path(...),
    media_id: str = Path(...),
    unit_id: str = Query(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Remove one file. A live post must keep at least two."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    await _service(db_connection, user_context).delete_media(
        contact_id=str(contact["id"]),
        listing_id=listing_id,
        media_id=media_id,
        unit_id=unit_id,
    )
    return success_response(
        request=request,
        message_key="marketplace.success.media_deleted",
        custom_code=CustomStatusCode.SUCCESS,
    )


@handle_api_exceptions("mark listing sold")
@router.post("/listings/{listing_id}/mark-sold", summary="Mark a listing sold")
@limiter.limit("30/minute")
async def mark_listing_sold(
    request: Request,
    listing_id: str = Path(...),
    body: MarkSoldRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Record the buyer and an optional private rating."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    data = await _service(db_connection, user_context).mark_sold(
        contact_id=str(contact["id"]), listing_id=listing_id, body=body
    )
    return success_response(
        request=request,
        message_key="marketplace.success.sold",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("save marketplace listing")
@router.put("/listings/{listing_id}/save", summary="Save or unsave a listing")
@limiter.limit("60/minute")
async def set_listing_saved(
    request: Request,
    listing_id: str = Path(...),
    body: SaveListingRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Bookmark a live listing, or clear that bookmark."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    service = _service(db_connection, user_context)
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


@handle_api_exceptions("report marketplace listing")
@router.post("/listings/{listing_id}/reports", status_code=http_status.HTTP_201_CREATED)
@limiter.limit("20/minute")
async def report_listing(
    request: Request,
    listing_id: str = Path(...),
    body: CreateReportRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Report a listing to the committee. The seller is not told."""
    user_context, contact = await extract_onboarding_contact_context(
        current_user, db_connection, request=request
    )
    data = await _service(db_connection, user_context).create_report(
        contact_id=str(contact["id"]), listing_id=listing_id, body=body
    )
    return success_response(
        request=request,
        message_key="marketplace.success.reported",
        status_code=http_status.HTTP_201_CREATED,
        custom_code=CustomStatusCode.CREATED,
        data=data,
    )


async def _visible_reports(service: MarketplaceService, user_context, db_connection, status):
    """Drop reports for societies this staff member cannot view."""
    reports = await service.list_reports(status=status)
    visible = []
    allowed: dict[str, bool] = {}
    for report in reports:
        project_id = report["project_id"]
        if project_id not in allowed:
            try:
                await ensure_staff_project_access_for_context(
                    user_context=user_context,
                    db_connection=db_connection,
                    project_id=project_id,
                    permission_codes=_VIEW,
                )
                allowed[project_id] = True
            except ForbiddenException:
                allowed[project_id] = False
        if allowed[project_id]:
            visible.append(report)
    return visible


@handle_api_exceptions("list marketplace reports")
@router.get("/reports", summary="Open marketplace reports")
@limiter.limit("60/minute")
async def list_reports(
    request: Request,
    status: str | None = Query(default="open"),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Reports in societies the staff member can view."""
    user_context = await extract_user_context(current_user, db_connection, request=request)
    service = _service(db_connection, user_context)
    data = await _visible_reports(service, user_context, db_connection, status)
    return success_response(
        request=request,
        message_key="marketplace.success.reports_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("review marketplace report")
@router.post("/reports/{report_id}/review", summary="Uphold or dismiss a report")
@limiter.limit("30/minute")
async def review_report(
    request: Request,
    report_id: str = Path(...),
    body: ReviewReportRequest = Body(...),
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Uphold takes the listing down. Dismiss leaves it live."""
    user_context = await extract_user_context(current_user, db_connection, request=request)
    service = _service(db_connection, user_context)
    report = await service.get_report(report_id=report_id)
    await ensure_staff_project_access_for_context(
        user_context=user_context,
        db_connection=db_connection,
        project_id=report["project_id"],
        permission_codes=_MODERATE,
    )
    reviewer_user_id = str(user_context.user_id)
    if body.decision == MarketplaceReportDecision.UPHOLD:
        note = (body.removal_note or "").strip()
        if not note:
            raise ValidationException(
                message_key="marketplace.errors.removal_note_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        data = await service.uphold_report(
            report_id=report_id,
            removal_note=note,
            reviewer_user_id=reviewer_user_id,
        )
        message_key = "marketplace.success.report_upheld"
    else:
        data = await service.dismiss_report(report_id=report_id, reviewer_user_id=reviewer_user_id)
        message_key = "marketplace.success.report_dismissed"
    return success_response(
        request=request,
        message_key=message_key,
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )


@handle_api_exceptions("marketplace feedback patterns")
@router.get("/feedback-patterns", summary="Sellers with repeated trouble")
@limiter.limit("60/minute")
async def feedback_patterns(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    current_user: dict = Depends(get_user_from_auth),
):
    """Sellers with three or more had_trouble ratings."""
    user_context = await extract_user_context(current_user, db_connection, request=request)
    service = _service(db_connection, user_context)
    project_ids = [
        str(row["project_id"])
        for row in await db_connection.fetch(
            """
            SELECT project_id
              FROM project_members
             WHERE organization_id = $1::uuid
               AND user_id = $2::uuid
               AND status = 'active'
            """,
            user_context.organization_id,
            user_context.user_id,
        )
    ]
    if not project_ids:
        fallback = await db_connection.fetchval(
            """
            SELECT id
              FROM projects
             WHERE organization_id = $1::uuid
             LIMIT 1
            """,
            user_context.organization_id,
        )
        if fallback:
            project_ids = [str(fallback)]
    allowed = False
    for project_id in project_ids:
        try:
            await ensure_staff_project_access_for_context(
                user_context=user_context,
                db_connection=db_connection,
                project_id=project_id,
                permission_codes=_VIEW,
            )
            allowed = True
            break
        except ForbiddenException:
            continue
    if not allowed:
        raise ForbiddenException(
            message_key="errors.insufficient_permissions",
            custom_code=CustomStatusCode.FORBIDDEN,
        )
    data = await service.feedback_patterns()
    return success_response(
        request=request,
        message_key="marketplace.success.patterns_retrieved",
        custom_code=CustomStatusCode.SUCCESS,
        data=data,
    )
