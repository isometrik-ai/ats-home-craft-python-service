"""Request models for resident buy and sell (ADR 0019)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums.marketplace import (
    MarketplaceItemCondition,
    MarketplaceListingKind,
    MarketplaceSaleRating,
)


class ListingMediaInput(BaseModel):
    """One image uploaded via presigned URL. Set only on create."""

    path: str = Field(min_length=1)
    file_type: str
    size_bytes: int = Field(gt=0, le=5_242_880)
    original_name: str | None = None
    sort_order: int = Field(ge=0)
    is_cover: bool = False


class CreateDraftListingRequest(BaseModel):
    """Step 2: create a draft (category, post fields, media). Does not go live."""

    unit_id: str
    category: str = Field(min_length=1, max_length=80)
    subtype: str | None = None
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
    """Partial edit. Media cannot change. A live row must remain publish-valid."""

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
    clear_price: bool = False
    clear_original_price: bool = False
    clear_brand: bool = False
    clear_product_url: bool = False


class PublishListingRequest(BaseModel):
    """Step 3 preview: go live. rules_accepted is required."""

    unit_id: str
    rules_accepted: bool


class RemoveListingRequest(BaseModel):
    """Soft-remove a live listing from the board."""

    unit_id: str
    removal_note: str = Field(min_length=1, max_length=500)


class RestoreListingRequest(BaseModel):
    """Restore a seller-removed listing back to live."""

    unit_id: str


class SaveListingRequest(BaseModel):
    """Bookmark or clear a bookmark."""

    unit_id: str
    saved: bool


class MarkSoldRequest(BaseModel):
    """Record the buyer and an optional private rating."""

    unit_id: str
    buyer_contact_id: str
    rating: MarketplaceSaleRating | None = None


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


class MarketplaceMineApiResponse(BaseModel):
    """API envelope for GET /marketplace/me/listings."""

    model_config = ConfigDict(extra="ignore")

    data: dict[str, Any]
