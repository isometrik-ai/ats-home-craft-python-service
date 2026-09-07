"""Invoice and payment request schemas."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.work_order_service.app.schemas.enums import (
    InvoiceStatus,
    PaymentMethod,
    PaymentStatus,
)


class CreateInvoiceRequest(BaseModel):
    """Request body for create invoice operations."""

    model_config = ConfigDict(extra="forbid")

    work_order_id: str
    company_id: str
    invoice_number: str = Field(..., min_length=1, max_length=100)
    invoice_date: date | None = None
    line_items: list[Any] = Field(default_factory=list)
    subtotal_minor: int = Field(default=0, ge=0)
    tax_minor: int = Field(default=0, ge=0)
    total_minor: int = Field(default=0, ge=0)
    currency: str | None = Field(None, max_length=10)
    status: InvoiceStatus | None = None
    file_paths: list[str] = Field(default_factory=list)
    timeline: list[Any] = Field(default_factory=list)
    revisions: list[Any] = Field(default_factory=list)


class UpdateInvoiceRequest(BaseModel):
    """Request body for update invoice operations."""

    model_config = ConfigDict(extra="forbid")

    invoice_number: str | None = Field(None, min_length=1, max_length=100)
    invoice_date: date | None = None
    line_items: list[Any] | None = None
    subtotal_minor: int | None = Field(None, ge=0)
    tax_minor: int | None = Field(None, ge=0)
    total_minor: int | None = Field(None, ge=0)
    currency: str | None = Field(None, max_length=10)
    status: InvoiceStatus | None = None
    file_paths: list[str] | None = None
    timeline: list[Any] | None = None
    revisions: list[Any] | None = None
    payment_id: str | None = None


class VendorSubmitInvoiceRequest(BaseModel):
    """Vendor portal invoice submission (WO and org context injected server-side)."""

    model_config = ConfigDict(extra="forbid")

    invoice_number: str = Field(..., min_length=1, max_length=100)
    company_id: str | None = None
    invoice_date: date | None = None
    line_items: list[Any] = Field(default_factory=list)
    subtotal_minor: int = Field(default=0, ge=0)
    tax_minor: int = Field(default=0, ge=0)
    total_minor: int = Field(default=0, ge=0)
    currency: str | None = Field(None, max_length=10)
    file_paths: list[str] = Field(default_factory=list)


class CreatePaymentRequest(BaseModel):
    """Request body for create payment operations."""

    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(..., ge=0)
    invoice_id: str | None = None
    work_order_id: str | None = None
    currency: str | None = Field(None, max_length=10)
    method: PaymentMethod | None = None
    reference: str | None = Field(None, max_length=255)
    payment_date: date | None = None
    status: PaymentStatus | None = None
    receipt_path: str | None = Field(None, max_length=2000)
    notes: str | None = Field(None, max_length=2000)


class UpdatePaymentRequest(BaseModel):
    """Request body for update payment operations."""

    model_config = ConfigDict(extra="forbid")

    amount_minor: int | None = Field(None, ge=0)
    currency: str | None = Field(None, max_length=10)
    method: PaymentMethod | None = None
    reference: str | None = Field(None, max_length=255)
    payment_date: date | None = None
    status: PaymentStatus | None = None
    receipt_path: str | None = Field(None, max_length=2000)
    notes: str | None = Field(None, max_length=2000)
