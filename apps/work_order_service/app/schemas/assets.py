"""Asset and asset category request schemas."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.work_order_service.app.schemas.enums import AssetStatus


class CreateAssetCategoryRequest(BaseModel):
    """Request body for create asset category operations."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    parent_id: str | None = None


class UpdateAssetCategoryRequest(BaseModel):
    """Request body for update asset category operations."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    parent_id: str | None = None


class CreateAssetRequest(BaseModel):
    """Request body for create asset operations."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=1, max_length=100)
    category_id: str
    make: str | None = Field(None, max_length=255)
    model: str | None = Field(None, max_length=255)
    serial_number: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=4000)
    status: AssetStatus | None = None
    facility_id: str | None = None
    location_text: str | None = Field(None, max_length=500)
    landmark_note: str | None = Field(None, max_length=500)
    photo_paths: list[str] = Field(default_factory=list)
    associated_parts: list[Any] = Field(default_factory=list)
    purchase_date: date | None = None
    purchase_cost_minor: int | None = Field(None, ge=0)
    currency: str | None = Field(None, max_length=10)
    supplier_name: str | None = Field(None, max_length=255)
    company_id: str | None = None
    purchase_order_number: str | None = Field(None, max_length=100)
    invoice_ref: str | None = Field(None, max_length=100)
    install_date: date | None = None
    warranty_start: date | None = None
    warranty_expiry: date | None = None
    warranty_terms: str | None = Field(None, max_length=2000)
    document_paths: list[str] = Field(default_factory=list)
    custom_fields: list[Any] = Field(default_factory=list)
    contract_id: str | None = None


class UpdateAssetRequest(BaseModel):
    """Request body for update asset operations."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    code: str | None = Field(None, min_length=1, max_length=100)
    category_id: str | None = None
    make: str | None = Field(None, max_length=255)
    model: str | None = Field(None, max_length=255)
    serial_number: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=4000)
    status: AssetStatus | None = None
    facility_id: str | None = None
    location_text: str | None = Field(None, max_length=500)
    landmark_note: str | None = Field(None, max_length=500)
    photo_paths: list[str] | None = None
    associated_parts: list[Any] | None = None
    purchase_date: date | None = None
    purchase_cost_minor: int | None = Field(None, ge=0)
    currency: str | None = Field(None, max_length=10)
    supplier_name: str | None = Field(None, max_length=255)
    company_id: str | None = None
    purchase_order_number: str | None = Field(None, max_length=100)
    invoice_ref: str | None = Field(None, max_length=100)
    install_date: date | None = None
    warranty_start: date | None = None
    warranty_expiry: date | None = None
    warranty_terms: str | None = Field(None, max_length=2000)
    document_paths: list[str] | None = None
    custom_fields: list[Any] | None = None
    contract_id: str | None = None
