"""Request models for resident buy and sell (ADR 0019)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from apps.user_service.app.schemas.enums.marketplace import (
    MarketplaceItemCondition,
    MarketplaceListingAction,
    MarketplaceListingKind,
    MarketplaceReportDecision,
    MarketplaceReportReason,
    MarketplaceSaleRating,
)


class CreateListingRequest(BaseModel):
    """Start a draft from a category."""

    unit_id: str
    category: str = Field(min_length=1, max_length=80)
    subtype: str | None = None


class UpdateListingRequest(BaseModel):
    """Partial edit. A live row must remain publish-valid after the patch."""

    unit_id: str | None = None
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
    clear_price: bool = False
    clear_original_price: bool = False
    clear_brand: bool = False
    clear_product_url: bool = False


class PublishListingRequest(BaseModel):
    """Post a draft. rules_accepted is required."""

    unit_id: str
    rules_accepted: bool


class AddListingMediaRequest(BaseModel):
    """Record a file already uploaded with a presigned URL."""

    unit_id: str
    path: str = Field(min_length=1)
    file_type: str
    size_bytes: int = Field(gt=0, le=5_242_880)
    original_name: str | None = None
    sort_order: int = Field(ge=0)
    is_cover: bool = False


class ListingActionRequest(BaseModel):
    """Publish, remove, restore, renew, or relist. rules_accepted is required to publish."""

    unit_id: str
    action: MarketplaceListingAction
    rules_accepted: bool | None = None


class SaveListingRequest(BaseModel):
    """Bookmark or clear a bookmark."""

    unit_id: str
    saved: bool


class MarkSoldRequest(BaseModel):
    """Record the buyer and an optional private rating."""

    unit_id: str
    buyer_contact_id: str
    rating: MarketplaceSaleRating | None = None


class CreateReportRequest(BaseModel):
    """Report a live listing to the committee."""

    unit_id: str
    reason: MarketplaceReportReason


class ReviewReportRequest(BaseModel):
    """Uphold takes the listing down. Dismiss leaves it live."""

    decision: MarketplaceReportDecision
    removal_note: str | None = Field(default=None, max_length=500)
