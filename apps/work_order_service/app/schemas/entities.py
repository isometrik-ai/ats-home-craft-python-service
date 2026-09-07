"""Entity response models for work_order_service OpenAPI docs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.work_order_service.app.schemas.enums import (
    AssetStatus,
    ContractStatus,
    InvoiceStatus,
    PaymentFrequency,
    PaymentMethod,
    PaymentStatus,
    TriggerEntity,
    TriggerEvent,
    VisitFrequency,
    WorkOrderPriority,
    WorkOrderSource,
    WorkOrderState,
)


class WorkOrderResponse(BaseModel):
    """Work order returned by staff and vendor APIs."""

    id: str
    organization_id: str
    project_id: str
    title: str
    description: str | None = None
    asset_ids: list[str] = Field(default_factory=list)
    contract_id: str | None = None
    company_id: str | None = None
    form_template_id: str | None = None
    pre_start_form_template_id: str | None = None
    state: WorkOrderState
    priority: WorkOrderPriority
    source: WorkOrderSource
    scheduled_date: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    assignee_name: str | None = None
    assignee_user_id: str | None = None
    line_items: list[Any] = Field(default_factory=list)
    form_values: dict[str, Any] = Field(default_factory=dict)
    pre_start_form_values: dict[str, Any] = Field(default_factory=dict)
    estimated_cost_minor: int | None = None
    access_notes: str | None = None
    is_recurring: bool = False
    recurring_frequency: VisitFrequency | None = None
    recurring_days: list[int] = Field(default_factory=list)
    recurring_end_date: str | None = None
    recurring_parent_id: str | None = None
    recurring_next_date: str | None = None
    invoice_ids: list[str] = Field(default_factory=list)
    vendor_token_hash: str | None = None
    timeline: list[Any] = Field(default_factory=list)
    termination_reason: str | None = None
    record_status: str = "active"
    deleted_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AssetCategoryResponse(BaseModel):
    """Asset category returned by the assets API."""

    id: str
    organization_id: str
    project_id: str
    name: str
    description: str = ""
    parent_id: str | None = None
    record_status: str = "active"
    deleted_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AssetResponse(BaseModel):
    """Asset returned by the assets API."""

    id: str
    organization_id: str
    project_id: str
    name: str
    code: str
    make: str | None = None
    model: str | None = None
    serial_number: str | None = None
    description: str | None = None
    category_id: str
    status: AssetStatus
    facility_id: str | None = None
    location_text: str | None = None
    landmark_note: str | None = None
    photo_paths: list[str] = Field(default_factory=list)
    associated_parts: list[Any] = Field(default_factory=list)
    purchase_date: str | None = None
    purchase_cost_minor: int | None = None
    currency: str = "INR"
    supplier_name: str | None = None
    company_id: str | None = None
    purchase_order_number: str | None = None
    invoice_ref: str | None = None
    install_date: str | None = None
    warranty_start: str | None = None
    warranty_expiry: str | None = None
    warranty_terms: str | None = None
    document_paths: list[str] = Field(default_factory=list)
    custom_fields: list[Any] = Field(default_factory=list)
    contract_id: str | None = None
    record_status: str = "active"
    deleted_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ContractResponse(BaseModel):
    """Maintenance contract returned by the contracts API."""

    id: str
    organization_id: str
    project_id: str
    title: str
    company_id: str
    asset_ids: list[str] = Field(default_factory=list)
    start_date: str
    end_date: str | None = None
    visit_frequency: VisitFrequency
    payment_frequency: PaymentFrequency
    value_minor: int | None = None
    currency: str = "INR"
    status: ContractStatus
    next_visit_date: str | None = None
    last_serviced_date: str | None = None
    auto_generate_lead_days: int | None = None
    scope_included: str | None = None
    scope_excluded: str | None = None
    form_template_id: str | None = None
    pre_start_form_template_id: str | None = None
    document_paths: list[str] = Field(default_factory=list)
    termination_reason: str | None = None
    record_status: str = "active"
    deleted_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class FormTemplateResponse(BaseModel):
    """Form template returned by the form templates API."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    organization_id: str
    project_id: str
    name: str
    description: str = ""
    form_schema: dict[str, Any] = Field(default_factory=dict, alias="schema")
    record_status: str = "active"
    deleted_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class InvoiceResponse(BaseModel):
    """Invoice returned by the invoices API."""

    id: str
    organization_id: str
    project_id: str
    work_order_id: str
    company_id: str
    invoice_number: str
    invoice_date: str | None = None
    line_items: list[Any] = Field(default_factory=list)
    subtotal_minor: int = 0
    tax_minor: int = 0
    total_minor: int = 0
    currency: str = "INR"
    status: InvoiceStatus
    file_paths: list[str] = Field(default_factory=list)
    timeline: list[Any] = Field(default_factory=list)
    revisions: list[Any] = Field(default_factory=list)
    payment_id: str | None = None
    record_status: str = "active"
    deleted_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PaymentResponse(BaseModel):
    """Payment returned by the payments API."""

    id: str
    organization_id: str
    project_id: str
    invoice_id: str | None = None
    work_order_id: str | None = None
    amount_minor: int
    currency: str = "INR"
    method: PaymentMethod
    reference: str | None = None
    payment_date: str | None = None
    status: PaymentStatus
    receipt_path: str | None = None
    notes: str | None = None
    record_status: str = "active"
    deleted_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class TriggerResponse(BaseModel):
    """Webhook trigger configuration returned by the integrations API."""

    id: str
    organization_id: str
    project_id: str
    name: str
    entity: TriggerEntity
    event: TriggerEvent
    is_active: bool = True
    webhook_url: str
    secret: str | None = None
    record_status: str = "active"
    deleted_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ApiKeyResponse(BaseModel):
    """Project API key metadata returned by the integrations API."""

    id: str
    organization_id: str
    project_id: str | None = None
    name: str
    key_prefix: str
    last_used_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Response payload for api key created."""

    key: str


class AuditEventResponse(BaseModel):
    """Response payload for audit event."""

    id: str
    organization_id: str
    project_id: str
    entity: TriggerEntity
    entity_id: str
    entity_label: str | None = None
    action: str
    actor_name: str | None = None
    actor_user_id: str | None = None
    source: str = "fm"
    changes: list[Any] = Field(default_factory=list)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class WebhookDeliveryResponse(BaseModel):
    """Recorded webhook delivery attempt."""

    id: str
    organization_id: str
    project_id: str
    trigger_id: str | None = None
    entity: TriggerEntity | None = None
    entity_id: str | None = None
    event: str
    request_payload: dict[str, Any] = Field(default_factory=dict)
    response_status: int | None = None
    error: str | None = None
    attempt: int = 1
    duration_ms: int = 0
    delivered: bool = False
    created_at: str | None = None
