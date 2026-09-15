"""Maintenance contract request schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from apps.work_order_service.app.schemas.enums import (
    ContractStatus,
    PaymentFrequency,
    VisitFrequency,
)


class CreateContractRequest(BaseModel):
    """Request body for create contract operations."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=500)
    vendor_id: str
    start_date: date
    asset_ids: list[str] = Field(default_factory=list)
    end_date: date | None = None
    visit_frequency: VisitFrequency | None = None
    payment_frequency: PaymentFrequency | None = None
    value_minor: int | None = Field(None, ge=0)
    currency: str | None = Field(None, max_length=10)
    status: ContractStatus | None = None
    next_visit_date: date | None = None
    last_serviced_date: date | None = None
    auto_generate_lead_days: int | None = Field(None, ge=0, le=365)
    scope_included: str | None = Field(None, max_length=8000)
    scope_excluded: str | None = Field(None, max_length=8000)
    form_template_id: str | None = None
    pre_start_form_template_id: str | None = None
    document_paths: list[str] = Field(default_factory=list)


class UpdateContractRequest(BaseModel):
    """Request body for update contract operations."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=500)
    vendor_id: str | None = None
    asset_ids: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None
    visit_frequency: VisitFrequency | None = None
    payment_frequency: PaymentFrequency | None = None
    value_minor: int | None = Field(None, ge=0)
    currency: str | None = Field(None, max_length=10)
    status: ContractStatus | None = None
    next_visit_date: date | None = None
    last_serviced_date: date | None = None
    auto_generate_lead_days: int | None = Field(None, ge=0, le=365)
    scope_included: str | None = Field(None, max_length=8000)
    scope_excluded: str | None = Field(None, max_length=8000)
    form_template_id: str | None = None
    pre_start_form_template_id: str | None = None
    document_paths: list[str] | None = None
    termination_reason: str | None = Field(None, max_length=2000)
