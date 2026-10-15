"""Request models for resident buy and sell (ADR 0018)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.enums.marketplace import (
    MarketplaceAdminStatus,
    MarketplaceItemCondition,
    MarketplaceListingKind,
    MarketplaceMediaKind,
    MarketplacePriceBand,
    MarketplaceReportDecision,
    MarketplaceReportReason,
    MarketplaceReportStatus,
    MarketplaceSaleRating,
    MarketplaceSoldType,
    MarketplaceSort,
)
from libs.shared_utils.status_codes import CustomStatusCode

_EXAMPLE_PROJECT_ID = "11111111-1111-1111-1111-111111111111"
_EXAMPLE_UNIT_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_EXAMPLE_LISTING_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_EXAMPLE_PUBLISHED_AT = "2026-10-01T08:30:00+00:00"
_EXAMPLE_EXPIRES_AT = "2026-10-31T08:30:00+00:00"
_EXAMPLE_REMOVED_AT = "2026-10-08T11:15:00+00:00"

_EXAMPLE_CATALOG_CATEGORY = {
    "slug": "furniture",
    "name": "Furniture",
    "icon": "sofa",
    "subtypes": [
        {"slug": "tables_desks", "name": "Tables & desks"},
        {"slug": "other", "name": "Other"},
    ],
}

_EXAMPLE_ADMIN_MEDIA = {
    "type": "image",
    "path": "org/listings/table.jpg",
    "file_type": "image/jpeg",
    "preview_path": "org/listings/table_preview.jpg",
    "description": "Front",
    "order": 1,
}

_EXAMPLE_ADMIN_CARD = {
    "id": _EXAMPLE_LISTING_ID,
    "title": "Study table with chair",
    "status": "live",
    "kind": "sale",
    "price_amount": "4500",
    "category": "furniture",
    "category_name": "Furniture",
    "subtype": "tables_desks",
    "subtype_name": "Tables & desks",
    "cover_path": "org/listings/table.jpg",
    "resident_name": "Rohan B.",
    "seller_photo_url": None,
    "project_id": _EXAMPLE_PROJECT_ID,
    "project_name": "ATS Nobility",
    "unit_label": "B-1104",
    "tower_name": "Tower B",
    "published_at": _EXAMPLE_PUBLISHED_AT,
    "expires_at": _EXAMPLE_EXPIRES_AT,
    "days_left": 22,
    "sold_at": None,
    "removed_at": None,
    "can_remove": True,
}

_EXAMPLE_ADMIN_DETAIL = {
    **_EXAMPLE_ADMIN_CARD,
    "description": "Used about a year. Pickup from Tower B.",
    "purchase_year": 2025,
    "age_years": 1,
    "negotiable": True,
    "brand": "Godrej",
    "condition": "lightly_used",
    "product_url": None,
    "original_price_amount": "9000",
    "original_bill_available": False,
    "show_flat_number": True,
    "seller_role": "Owner",
    "member_since_year": 2021,
    "collection_latitude": 28.4089,
    "collection_longitude": 77.3178,
    "media": [_EXAMPLE_ADMIN_MEDIA],
    "removal_note": None,
    "listings_from_unit_total": 3,
    "listings_from_unit_active": 2,
    "removed_before_count": 1,
    "isometrik_id": "6a4fb3e2f96daa0001281d6c",
}

_EXAMPLE_ADMIN_REMOVED = {
    **_EXAMPLE_ADMIN_DETAIL,
    "status": "removed",
    "can_remove": False,
    "days_left": None,
    "removed_at": _EXAMPLE_REMOVED_AT,
    "removal_note": "Photos didn't show the actual item",
}


class ListingMediaInput(BaseModel):
    """One image or video stored on listings.media. Set only on create."""

    type: MarketplaceMediaKind
    path: str = Field(min_length=1)
    file_type: str
    preview_path: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, max_length=200)
    order: int = Field(ge=1)

    @model_validator(mode="after")
    def preview_required_for_video(self) -> ListingMediaInput:
        """Videos need a poster path; images may omit it."""
        if self.type == MarketplaceMediaKind.VIDEO and not (self.preview_path or "").strip():
            raise ValueError("preview_path is required when type is video")
        return self


class BrowseListingsQuery(BaseModel):
    """Query params for GET /marketplace/listings."""

    model_config = ConfigDict(extra="forbid")

    category: str | None = Field(default=None, description="Catalog category slug.")
    subtype: str | None = Field(default=None, description="Catalog subtype slug.")
    q: str | None = Field(default=None, description="Search title text.")
    sort: MarketplaceSort = MarketplaceSort.NEWEST
    price_band: MarketplacePriceBand | None = None
    condition: list[MarketplaceItemCondition] | None = Field(
        default=None,
        description="Repeatable. lightly_used, well_used, needs_repair.",
    )
    contact_id: UUID | None = Field(
        default=None,
        description="Optional seller contact UUID. Limits results to that contact's live listings.",
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1)


class CreateListingRequest(BaseModel):
    """Create a listing (starts unpublished). Publish separately to go live."""

    unit_id: str
    category: str = Field(min_length=1, max_length=80, description="Catalog category slug.")
    subtype: str | None = Field(default=None, description="Catalog subtype slug. Required.")
    title: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    purchase_year: int | None = None
    kind: MarketplaceListingKind = MarketplaceListingKind.SALE
    price_amount: Decimal | None = None
    original_price_amount: Decimal | None = None
    negotiable: bool = False
    brand: str | None = Field(default=None, max_length=80)
    condition: MarketplaceItemCondition | None = None
    product_url: str | None = None
    show_flat_number: bool = False
    original_bill_available: bool = False
    media: list[ListingMediaInput] = Field(default_factory=list, max_length=8)


class UpdateListingRequest(BaseModel):
    """Partial edit. Status and media cannot change. A live row must remain publish-valid."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    pickup_unit_id: str | None = None
    title: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    purchase_year: int | None = None
    kind: MarketplaceListingKind | None = None
    price_amount: Decimal | None = None
    original_price_amount: Decimal | None = None
    negotiable: bool | None = None
    brand: str | None = Field(default=None, max_length=80)
    condition: MarketplaceItemCondition | None = None
    product_url: str | None = None
    show_flat_number: bool | None = None
    original_bill_available: bool | None = None


class RemoveListingRequest(BaseModel):
    """Delete a draft listing or soft-remove a live listing from the board."""

    unit_id: str
    removal_note: str = Field(min_length=1, max_length=500)


class SaveListingRequest(BaseModel):
    """Bookmark or clear a bookmark."""

    unit_id: str
    saved: bool


class ReportListingRequest(BaseModel):
    """Send a listing to the committee."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "unit_id": _EXAMPLE_UNIT_ID,
                "reason": "business_or_broker",
            }
        },
    )

    unit_id: str
    reason: MarketplaceReportReason = Field(
        ...,
        description="Slug from GET /marketplace/report-reasons.",
    )


class MarketplaceReportCreated(BaseModel):
    """Payload after a resident reports a listing."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "id": "323d45d8-ba1b-4bf9-988c-ede6043fe566",
                "listing_id": _EXAMPLE_LISTING_ID,
                "reason": "Looks like a business or broker",
                "listing_report_count": 1,
                "listing_hidden": False,
            }
        },
    )

    id: str
    listing_id: str
    reason: str = Field(..., description="Resident-facing reason label, not the stored slug.")
    listing_report_count: int = Field(..., ge=1)
    listing_hidden: bool = Field(
        ...,
        description="True when three reports hide the listing from the resident board.",
    )


class MarketplaceReportCreatedApiResponse(BaseModel):
    """API envelope for POST /marketplace/listings/{id}/report."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Report sent to the committee.",
                "statusCode": 201,
                "code": CustomStatusCode.SUCCESS.value,
                "data": MarketplaceReportCreated.model_config["json_schema_extra"]["example"],
            }
        },
    )

    status: str
    message: str
    statusCode: int
    code: str
    data: MarketplaceReportCreated


class MarkSoldRequest(BaseModel):
    """Mark a live listing sold to a MyNest resident or someone outside the app."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    sold_type: MarketplaceSoldType = MarketplaceSoldType.INSIDE
    buyer_contact_id: UUID | None = Field(
        default=None,
        description="Required when sold_type is inside (MyNest buyer contact UUID).",
    )
    rating: MarketplaceSaleRating | None = Field(
        default=None,
        description="Optional private rating; only when sold_type is inside.",
    )

    @model_validator(mode="after")
    def buyer_matches_sold_type(self) -> MarkSoldRequest:
        """Inside requires a buyer contact; outside must not send one."""
        if self.sold_type == MarketplaceSoldType.INSIDE:
            if self.buyer_contact_id is None:
                raise ValueError("buyer_contact_id is required when sold_type is inside")
        elif self.buyer_contact_id is not None:
            raise ValueError("buyer_contact_id must be omitted when sold_type is outside")
        if self.sold_type == MarketplaceSoldType.OUTSIDE and self.rating is not None:
            raise ValueError("rating must be omitted when sold_type is outside")
        return self


class AdminMarketplaceListQuery(BaseModel):
    """Query params for GET /marketplace/admin/listings."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": _EXAMPLE_PROJECT_ID,
                "q": "table",
                "status": "live",
                "category": "furniture",
                "page": 1,
                "page_size": 20,
            }
        },
    )

    project_id: str | None = Field(
        default=None,
        description="Optional society filter (UUID). Omit to list the whole organization.",
    )
    q: str | None = Field(
        default=None,
        description="Search item title, resident name, unit, or tower.",
    )
    status: MarketplaceAdminStatus = MarketplaceAdminStatus.ALL
    category: str | None = Field(default=None, description="Catalog category slug.")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AdminMarketplaceReportListQuery(BaseModel):
    """Query params for GET /marketplace/admin/reports."""

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = Field(
        default=None,
        description="Optional society filter (UUID). Omit for the whole organization.",
    )
    status: MarketplaceReportStatus | None = Field(
        default=None,
        description="Filter by report status. Omit for all.",
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AdminRemoveListingRequest(BaseModel):
    """Staff take-down of a live listing. Removal is permanent."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"removal_note": "Photos didn't show the actual item"},
        },
    )

    removal_note: str = Field(
        min_length=1,
        max_length=500,
        description="Note the seller will see. Removal is permanent.",
    )


class AdminReviewMarketplaceReportRequest(BaseModel):
    """Staff decision on one open report (Reported tab: dismiss or remove listing)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"decision": "dismiss"},
                {
                    "decision": "uphold",
                    "removal_note": "Not allowed in the society",
                },
            ]
        },
    )

    decision: MarketplaceReportDecision = Field(
        ...,
        description="dismiss = false report; uphold = soft-remove the listing.",
    )
    removal_note: str | None = Field(
        default=None,
        max_length=500,
        description="Required when decision is uphold. Shown to the seller.",
    )

    @model_validator(mode="after")
    def uphold_requires_removal_note(self) -> AdminReviewMarketplaceReportRequest:
        """Uphold removes the listing and needs a staff note."""
        if self.decision == MarketplaceReportDecision.UPHOLD:
            note = (self.removal_note or "").strip()
            if not note:
                raise ValueError("removal_note is required when decision is uphold")
        return self


class MarketplaceCatalogSubtype(BaseModel):
    """One subtype in the static marketplace catalog."""

    model_config = ConfigDict(extra="ignore")

    slug: str
    name: str


class MarketplaceCatalogCategory(BaseModel):
    """One category in the static marketplace catalog."""

    model_config = ConfigDict(extra="ignore")

    slug: str
    name: str
    icon: str | None = None
    subtypes: list[MarketplaceCatalogSubtype] = Field(default_factory=list)


class MarketplaceCatalogData(BaseModel):
    """Category list returned by GET /marketplace/catalog."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={"example": {"categories": [_EXAMPLE_CATALOG_CATEGORY]}},
    )

    categories: list[MarketplaceCatalogCategory]


class MarketplaceCatalogApiResponse(BaseModel):
    """API envelope for GET /marketplace/catalog and /marketplace/admin/catalog."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Categories retrieved successfully.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": {"categories": [_EXAMPLE_CATALOG_CATEGORY]},
            }
        },
    )

    status: str
    message: str
    statusCode: int
    code: str
    data: MarketplaceCatalogData


_EXAMPLE_REPORT_REASON = {
    "slug": "not_allowed",
    "name": "Not allowed in the society",
}

_EXAMPLE_REPORT_REASON_CATALOG = {
    "sheet_title": "Report this listing",
    "sheet_subtitle": "It goes to the society committee, not to the seller.",
    "reasons": [
        _EXAMPLE_REPORT_REASON,
        {"slug": "business_or_broker", "name": "Looks like a business or broker"},
        {"slug": "sold_but_listed", "name": "Sold but still listed"},
        {"slug": "something_else", "name": "Something else"},
    ],
}


class MarketplaceReportReasonOption(BaseModel):
    """One selectable reason on the report sheet."""

    model_config = ConfigDict(extra="ignore")

    slug: str = Field(..., description="Stored on marketplace_reports.reason.")
    name: str = Field(..., description="Resident-facing label.")


class MarketplaceReportReasonCatalogData(BaseModel):
    """Copy and options for the report listing sheet."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={"example": _EXAMPLE_REPORT_REASON_CATALOG},
    )

    sheet_title: str | None = None
    sheet_subtitle: str | None = None
    reasons: list[MarketplaceReportReasonOption] = Field(default_factory=list)


class MarketplaceReportReasonCatalogApiResponse(BaseModel):
    """API envelope for GET /marketplace/report-reasons."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Report reasons retrieved successfully.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": _EXAMPLE_REPORT_REASON_CATALOG,
            }
        },
    )

    status: str
    message: str
    statusCode: int
    code: str
    data: MarketplaceReportReasonCatalogData


class MarketplaceListingApiResponse(BaseModel):
    """API envelope for a single resident listing."""

    model_config = ConfigDict(extra="ignore")

    data: dict[str, Any]


class MarketplaceListApiResponse(BaseModel):
    """API envelope for paginated resident listing cards."""

    model_config = ConfigDict(extra="ignore")

    data: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


class MarketplaceAdminSummary(BaseModel):
    """Header counts for the staff buy-and-sell board."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "active_count": 12,
                "sold_count": 2,
                "past_count": 1,
                "removed_count": 3,
            }
        },
    )

    active_count: int = Field(..., description="Live listings on the board.")
    sold_count: int = Field(..., description="Listings marked sold.")
    past_count: int = Field(..., description="Expired listings.")
    removed_count: int = Field(..., description="Listings taken down by seller or staff.")


class MarketplaceAdminSummaryApiResponse(BaseModel):
    """API envelope for GET /marketplace/admin/summary."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Marketplace summary retrieved successfully.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": {
                    "active_count": 12,
                    "sold_count": 2,
                    "past_count": 1,
                    "removed_count": 3,
                },
            }
        },
    )

    status: str
    message: str
    statusCode: int
    code: str
    data: MarketplaceAdminSummary


class MarketplaceListingMediaItem(BaseModel):
    """One image or video on a listing."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={"example": _EXAMPLE_ADMIN_MEDIA},
    )

    type: MarketplaceMediaKind
    path: str
    file_type: str | None = None
    preview_path: str | None = None
    description: str | None = None
    order: int | None = None


class MarketplaceAdminListingCard(BaseModel):
    """One row on the staff listings table."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={"example": _EXAMPLE_ADMIN_CARD},
    )

    id: str
    title: str | None = None
    status: str
    kind: str
    price_amount: str | None = None
    category: str
    category_name: str | None = None
    subtype: str | None = None
    subtype_name: str | None = None
    cover_path: str | None = None
    resident_name: str | None = None
    seller_photo_url: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    unit_label: str | None = None
    tower_name: str | None = None
    published_at: str | None = None
    expires_at: str | None = None
    days_left: int | None = None
    sold_at: str | None = None
    removed_at: str | None = None
    can_remove: bool


class MarketplaceAdminListingDetail(MarketplaceAdminListingCard):
    """Staff drawer payload for one listing."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={"example": _EXAMPLE_ADMIN_DETAIL},
    )

    description: str | None = None
    purchase_year: int | None = None
    age_years: int | None = None
    negotiable: bool | None = None
    brand: str | None = None
    condition: str | None = None
    product_url: str | None = None
    original_price_amount: str | None = None
    original_bill_available: bool | None = None
    show_flat_number: bool | None = None
    seller_role: str | None = None
    member_since_year: int | None = None
    collection_latitude: float | None = None
    collection_longitude: float | None = None
    media: list[MarketplaceListingMediaItem] = Field(default_factory=list)
    removal_note: str | None = None
    listings_from_unit_total: int = 0
    listings_from_unit_active: int = 0
    removed_before_count: int = 0
    isometrik_id: str | None = Field(
        default=None,
        description="Seller Isometrik user id (contacts.isometrik_user_id). Detail API only.",
    )


class MarketplaceAdminListApiResponse(BaseModel):
    """API envelope for GET /marketplace/admin/listings."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Listings retrieved successfully.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": [_EXAMPLE_ADMIN_CARD],
                "total": 1,
                "page": 1,
                "page_size": 20,
                "total_pages": 1,
            }
        },
    )

    status: str
    message: str
    statusCode: int
    code: str
    data: list[MarketplaceAdminListingCard]
    total: int
    page: int
    page_size: int
    total_pages: int


class MarketplaceAdminListingApiResponse(BaseModel):
    """API envelope for GET /marketplace/admin/listings/{id}."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Listing retrieved successfully.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": _EXAMPLE_ADMIN_DETAIL,
            }
        },
    )

    status: str
    message: str
    statusCode: int
    code: str
    data: MarketplaceAdminListingDetail


_EXAMPLE_ADMIN_REPORT_ROW = {
    "id": "323d45d8-ba1b-4bf9-988c-ede6043fe566",
    "listing_id": _EXAMPLE_LISTING_ID,
    "project_id": _EXAMPLE_PROJECT_ID,
    "title": "Maruti Baleno 2019, petrol",
    "item_subtitle": "Vehicles - BS-1021",
    "cover_path": "org/listings/table.jpg",
    "reason": "Looks like a business or broker",
    "reporter_name": "Sneha Varma",
    "reporter_unit_label": "F-0204",
    "reported_at": _EXAMPLE_PUBLISHED_AT,
    "listing_report_count": 1,
    "status": "open",
}


class MarketplaceAdminReportRow(BaseModel):
    """One row on the staff Reported tab."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={"example": _EXAMPLE_ADMIN_REPORT_ROW},
    )

    id: str
    listing_id: str
    project_id: str
    title: str | None = None
    item_subtitle: str | None = Field(
        default=None,
        description="Category name and listing unit, e.g. Vehicles - BS-1021.",
    )
    cover_path: str | None = None
    reason: str = Field(..., description="Resident-facing reason label, not the stored slug.")
    reporter_name: str | None = None
    reporter_unit_label: str | None = None
    reported_at: str | None = None
    listing_report_count: int = Field(
        ...,
        ge=1,
        description="Total reports on this listing in the organization.",
    )
    status: str


class MarketplaceAdminReportListApiResponse(BaseModel):
    """API envelope for GET /marketplace/admin/reports."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Reports retrieved successfully.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": [_EXAMPLE_ADMIN_REPORT_ROW],
                "total": 1,
                "page": 1,
                "page_size": 20,
                "total_pages": 1,
            }
        },
    )

    status: str
    message: str
    statusCode: int
    code: str
    data: list[MarketplaceAdminReportRow]
    total: int
    page: int
    page_size: int
    total_pages: int


class MarketplaceAdminReportDismissApiResponse(BaseModel):
    """API envelope for POST /marketplace/admin/reports/{id}/review (dismiss)."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Report dismissed.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": {**_EXAMPLE_ADMIN_REPORT_ROW, "status": "dismissed"},
            }
        },
    )

    status: str
    message: str
    statusCode: int
    code: str
    data: MarketplaceAdminReportRow


class MarketplaceAdminReportReviewResult(BaseModel):
    """Report row plus listing after uphold (remove listing from Reported tab)."""

    model_config = ConfigDict(extra="ignore")

    report: MarketplaceAdminReportRow
    listing: MarketplaceAdminListingDetail


class MarketplaceAdminReportReviewApiResponse(BaseModel):
    """API envelope for POST /marketplace/admin/reports/{id}/review (uphold)."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Listing taken down.",
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": {
                    "report": {**_EXAMPLE_ADMIN_REPORT_ROW, "status": "upheld"},
                    "listing": {"id": _EXAMPLE_LISTING_ID, "status": "removed"},
                },
            }
        },
    )

    status: str
    message: str
    statusCode: int
    code: str
    data: MarketplaceAdminReportReviewResult


class MarketplaceAdminRemovedApiResponse(BaseModel):
    """API envelope for POST /marketplace/admin/listings/{id}/remove."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "status": "success",
                "message": (
                    "Listing marked as removed. It stays in admin under Removed "
                    "and is hidden from the resident board."
                ),
                "statusCode": 200,
                "code": CustomStatusCode.SUCCESS.value,
                "data": _EXAMPLE_ADMIN_REMOVED,
            }
        },
    )

    status: str
    message: str
    statusCode: int
    code: str
    data: MarketplaceAdminListingDetail
