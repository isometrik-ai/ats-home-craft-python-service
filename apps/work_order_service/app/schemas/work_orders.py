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
    company_id: str | None = None
    state: WorkOrderState | None = None
    priority: WorkOrderPriority | None = None
    source: WorkOrderSource | None = None
    scheduled_date: date | None = None
    assignee_name: str | None = Field(None, max_length=255)
    assignee_user_id: str | None = None
    access_notes: str | None = Field(None, max_length=4000)
    form_template_id: str | None = None
    is_recurring: bool = False
    recurring_frequency: VisitFrequency | None = None
    recurring_days: list[int] = Field(default_factory=list)
    recurring_end_date: date | None = None
    recurring_parent_id: str | None = None


class UpdateWorkOrderRequest(BaseModel):
    """Partial update for a work order (staff)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = Field(None, max_length=8000)
    state: WorkOrderState | None = None
    priority: WorkOrderPriority | None = None
    scheduled_date: date | None = None
    timeline: list[dict[str, Any]] | None = None
    company_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    termination_reason: str | None = Field(None, max_length=2000)


class VendorUpdateWorkOrderRequest(BaseModel):
    """Vendor portal partial update (restricted fields)."""

    model_config = ConfigDict(extra="forbid")

    state: WorkOrderState | None = None
    timeline: list[dict[str, Any]] | None = None
    form_values: dict[str, Any] | None = None
