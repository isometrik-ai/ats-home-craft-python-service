"""Tenant request schemas."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.common import Email, Phone
from apps.user_service.app.schemas.contact_onboarding import (
    _validate_exactly_one_primary_phone,
)
from apps.user_service.app.schemas.enums import (
    TENANT_REQUEST_REQUIRED_DOCUMENT_TYPES,
    TenantRequestDocumentType,
    TenantRequestListBucket,
    TenantRequestStatus,
)


class TenantRequestDocumentInput(BaseModel):
    """Document upload slot on create or re-upload."""

    model_config = ConfigDict(extra="forbid")

    document_type: TenantRequestDocumentType
    file_path: str = Field(..., min_length=1, max_length=2000)
    file_name: str | None = Field(None, max_length=255)


class CreateTenantRequestRequest(BaseModel):
    """Owner submits a tenant request with all required documents."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    first_name: str = Field(..., max_length=100)
    last_name: str | None = Field(None, max_length=100)
    phones: list[Phone] = Field(..., min_length=1, max_length=20)
    emails: list[Email] | None = Field(None, max_length=20)
    move_in_date: date | None = None
    portal_access: bool = False
    documents: list[TenantRequestDocumentInput] = Field(..., min_length=3, max_length=3)

    @field_validator("phones")
    @classmethod
    def validate_primary_phone(cls, phones: list[Phone]) -> list[Phone]:
        """Validate exactly one primary phone."""
        return _validate_exactly_one_primary_phone(phones)

    @field_validator("documents")
    @classmethod
    def validate_required_document_types(
        cls,
        documents: list[TenantRequestDocumentInput],
    ) -> list[TenantRequestDocumentInput]:
        """Require exactly one row per mandatory document type."""
        provided = {item.document_type for item in documents}
        required = set(TENANT_REQUEST_REQUIRED_DOCUMENT_TYPES)
        if provided != required:
            raise ValueError(
                "documents must include id_proof, rental_agreement, and police_verification"
            )
        return documents


class ReuploadTenantDocumentRequest(BaseModel):
    """Replace a rejected document file."""

    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(..., min_length=1, max_length=2000)
    file_name: str | None = Field(None, max_length=255)


class RejectTenantDocumentRequest(BaseModel):
    """Admin rejects a document."""

    model_config = ConfigDict(extra="forbid")

    rejection_reason: str = Field(..., min_length=1, max_length=2000)


class ApproveTenantRequestRequest(BaseModel):
    """Admin approves a ready tenant request."""

    model_config = ConfigDict(extra="forbid")

    move_in_date: date
    admin_notes: str | None = Field(None, max_length=2000)


class TenantRequestListQuery(BaseModel):
    """Query params for admin GET /projects/{project_id}/tenant-requests."""

    model_config = ConfigDict(extra="forbid")

    bucket: TenantRequestListBucket | None = None
    status: TenantRequestStatus | None = None
    search: str | None = Field(None, max_length=200)
    unit_id: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class OwnerTenantRequestListQuery(BaseModel):
    """Query params for owner GET /contact-onboarding/tenant-requests."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class TenantRequestDocumentResponse(BaseModel):
    """Document row on a tenant request."""

    model_config = ConfigDict(extra="ignore")

    id: str
    document_type: str
    file_path: str
    file_name: str | None = None
    status: str
    rejection_reason: str | None = None
    verified_at: str | None = None
    uploaded_at: str | None = None


class TenantRequestEventResponse(BaseModel):
    """Timeline event on a tenant request."""

    model_config = ConfigDict(extra="ignore")

    id: str
    event_type: str
    occurred_at: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TenantRequestMilestoneResponse(BaseModel):
    """Derived mobile timeline milestone."""

    model_config = ConfigDict(extra="ignore")

    key: str
    label: str
    completed: bool
    occurred_at: str | None = None


class TenantRequestOwnerSummary(BaseModel):
    """Owner (submitter) summary on admin tenant request rows."""

    model_config = ConfigDict(extra="ignore")

    contact_id: str
    display_name: str | None = None
    phone: str | None = None
    email: str | None = None
    profile_photo_url: str | None = None


class TenantRequestUnitSummary(BaseModel):
    """Unit summary on admin tenant request rows."""

    model_config = ConfigDict(extra="ignore")

    id: str
    code: str
    unit_label: str | None = None
    location_label: str | None = None
    property_type: str | None = None
    config_kind: str | None = None
    floor_level_number: int | None = None
    floor_display_name: str | None = None
    config_display_label: str | None = None
    tower_id: str | None = None
    config_id: str | None = None
    status: str
    sort_order: int = 0


class TenantRequestListItemResponse(BaseModel):
    """Tenant request row for the admin project list table."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    unit_id: str
    submitted_by_contact_id: str
    owner_name: str | None = None
    tenant_first_name: str
    tenant_last_name: str | None = None
    tenant_phones: list[dict[str, Any]] = Field(default_factory=list)
    tenant_emails: list[dict[str, Any]] = Field(default_factory=list)
    move_in_date: str | None = None
    status: str
    portal_access: bool = False
    submitted_at: str | None = None
    approved_at: str | None = None
    cancelled_at: str | None = None
    documents_verified_count: int = 0
    documents_total_count: int = 3
    owner: TenantRequestOwnerSummary | None = None
    unit: TenantRequestUnitSummary | None = None
    created_at: str | None = None
    updated_at: str | None = None


class TenantRequestResponse(BaseModel):
    """Tenant request detail response."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    unit_id: str
    unit_code: str | None = None
    unit_label: str | None = None
    submitted_by_contact_id: str
    owner_name: str | None = None
    tenant_first_name: str
    tenant_last_name: str | None = None
    tenant_phones: list[dict[str, Any]] = Field(default_factory=list)
    tenant_emails: list[dict[str, Any]] = Field(default_factory=list)
    move_in_date: str | None = None
    status: str
    portal_access: bool = False
    tenant_contact_id: str | None = None
    contact_unit_id: str | None = None
    submitted_at: str | None = None
    approved_at: str | None = None
    superseded_at: str | None = None
    cancelled_at: str | None = None
    admin_notes: str | None = None
    documents: list[TenantRequestDocumentResponse] = Field(default_factory=list)
    events: list[TenantRequestEventResponse] = Field(default_factory=list)
    milestones: list[TenantRequestMilestoneResponse] = Field(default_factory=list)
    documents_verified_count: int = 0
    documents_total_count: int = 3
    owner: TenantRequestOwnerSummary | None = None
    unit: TenantRequestUnitSummary | None = None
    created_at: str | None = None
    updated_at: str | None = None


class TenantRequestSummaryResponse(BaseModel):
    """Admin dashboard summary cards."""

    model_config = ConfigDict(extra="ignore")

    pending_review: int = 0
    awaiting_resubmission: int = 0
    ready_to_approve: int = 0
    approved_this_month: int = 0
