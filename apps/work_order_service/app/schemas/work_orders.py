"""Work order request schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.work_order_service.app.schemas.enums import (
    VisitFrequency,
    WorkOrderPriority,
    WorkOrderSource,
    WorkOrderState,
)


class CreateWorkOrderRequest(BaseModel):
    """Create a work order (staff)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(None, max_length=8000)
    asset_ids: list[str] = Field(default_factory=list)
    contract_id: str | None = None
    vendor_id: str | None = None
    state: WorkOrderState | None = None
    priority: WorkOrderPriority | None = None
    source: WorkOrderSource | None = None
    scheduled_date: date | None = None
    assignee_name: str | None = Field(None, max_length=255)
    assignee_user_id: str | None = None
    access_notes: str | None = Field(None, max_length=4000)
    form_template_id: str | None = None
    pre_start_form_template_id: str | None = None
    is_recurring: bool = False
    recurring_frequency: VisitFrequency | None = None
    recurring_days: list[int] = Field(default_factory=list)
    recurring_end_date: date | None = None
    recurring_parent_id: str | None = None
    estimated_cost_minor: int | None = Field(None, ge=0)
    line_items: list[Any] = Field(default_factory=list)
    form_values: dict[str, Any] = Field(default_factory=dict)
    pre_start_form_values: dict[str, Any] = Field(default_factory=dict)


class UpdateWorkOrderRequest(BaseModel):
    """Partial update for a work order (staff).

    Timeline is append-only — use POST .../work-orders/{id}/timeline instead.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = Field(None, max_length=8000)
    asset_ids: list[str] | None = None
    state: WorkOrderState | None = None
    priority: WorkOrderPriority | None = None
    scheduled_date: date | None = None
    vendor_id: str | None = None
    assignee_name: str | None = Field(None, max_length=255)
    assignee_user_id: str | None = None
    access_notes: str | None = Field(None, max_length=4000)
    form_template_id: str | None = None
    pre_start_form_template_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    estimated_cost_minor: int | None = Field(None, ge=0)
    line_items: list[Any] | None = None
    form_values: dict[str, Any] | None = None
    pre_start_form_values: dict[str, Any] | None = None
    invoice_ids: list[str] | None = None
    termination_reason: str | None = Field(None, max_length=2000)
    is_recurring: bool | None = None
    recurring_frequency: VisitFrequency | None = None
    recurring_days: list[int] | None = None
    recurring_end_date: date | None = None


class VendorUpdateWorkOrderRequest(BaseModel):
    """Vendor portal partial update (restricted fields)."""

    model_config = ConfigDict(extra="forbid")

    state: WorkOrderState | None = None
    line_items: list[Any] | None = None
    form_values: dict[str, Any] | None = None
    pre_start_form_values: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
