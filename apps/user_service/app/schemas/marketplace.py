"""Request models for resident buy and sell (ADR 0018)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.enums.marketplace import (
    MarketplaceAdminStatus,
    MarketplaceItemCondition,
    MarketplaceListingKind,
    MarketplaceMediaKind,
    MarketplacePriceBand,
    MarketplaceSaleRating,
    MarketplaceSort,
)


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


class PublishListingRequest(BaseModel):
    """Go live from an unpublished listing."""

    unit_id: str


class RemoveListingRequest(BaseModel):
    """Soft-remove a live listing from the board."""

    unit_id: str
    removal_note: str = Field(min_length=1, max_length=500)


class SaveListingRequest(BaseModel):
    """Bookmark or clear a bookmark."""

    unit_id: str
    saved: bool


class MarkSoldRequest(BaseModel):
    """Record the buyer and an optional private rating."""

    unit_id: str
    buyer_contact_id: str
    rating: MarketplaceSaleRating | None = None


class AdminMarketplaceListQuery(BaseModel):
    """Query params for GET /marketplace/admin/listings."""

    model_config = ConfigDict(extra="forbid")

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


class AdminRemoveListingRequest(BaseModel):
    """Staff take-down of a live listing. Removal is permanent."""

    removal_note: str = Field(min_length=1, max_length=500)


class MarketplaceCatalogApiResponse(BaseModel):
    """API envelope for GET /marketplace/catalog."""

    model_config = ConfigDict(extra="ignore")

    data: dict[str, Any]


class MarketplaceListingApiResponse(BaseModel):
    """API envelope for a single listing."""

    model_config = ConfigDict(extra="ignore")

    data: dict[str, Any]


class MarketplaceListApiResponse(BaseModel):
    """API envelope for paginated listing cards."""

    model_config = ConfigDict(extra="ignore")

    data: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


class MarketplaceSummaryApiResponse(BaseModel):
    """API envelope for staff header counts."""

    model_config = ConfigDict(extra="ignore")

    data: dict[str, Any]
