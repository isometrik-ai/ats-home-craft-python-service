"""Leads Schemas Module.

Pydantic models for lead create, update, list, detail, and query operations.
Aligned with ``public.leads`` v2 and ``public.lead_contacts``.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from apps.user_service.app.schemas.clients import Phone
from apps.user_service.app.schemas.enums import DealType, LeadsListMode, Priority
from apps.user_service.app.schemas.lead_stages import UNSET, Unset
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class LeadNoteItem(BaseModel):
    """One structured note in ``leads.notes`` (JSONB array)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., max_length=500)
    content: str = Field(..., max_length=50000)

    @field_validator("title", "content", mode="before")
    @classmethod
    def strip_whitespace(cls, value: str) -> str:
        """Strip whitespace; treat blank strings as unset (``None``)."""
        return value.strip()

    @field_validator("title", "content")
    @classmethod
    def non_empty_after_strip(cls, value: str) -> str:
        """Raise ValueError if stripped value is empty."""
        if not value:
            raise ValueError("must not be empty")
        return value


class LeadContactCreate(BaseModel):
    """Person client linked to a lead (``lead_contacts``)."""

    model_config = ConfigDict(extra="forbid")

    contact_client_id: str = Field(..., description="Person client UUID")
    label: str | None = Field(
        default=None,
        max_length=255,
        description="Optional role or tag (e.g. decision_maker)",
    )

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        """Strip whitespace; blank becomes ``None``."""
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class CreateLeadRequest(BaseModel):
    """Request body for ``POST /leads`` (v2 ``public.leads``)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Lead display title")
    stage_id: str = Field(
        ...,
        description="Pipeline stage UUID (must belong to the organization)",
    )
    lead_source: str | None = Field(default=None, max_length=255, description="Origin channel")
    referral_source: str | None = Field(
        default=None,
        max_length=255,
        description="Referrer name or id",
    )
    lead_score: str | None = Field(default=None, max_length=255, description="Score label or tier")
    close_date: date | None = Field(
        default=None,
        description="Expected close date (YYYY-MM-DD)",
    )
    amount: Decimal | None = Field(default=None, description="Estimated deal value")
    description: str | None = Field(
        default=None,
        max_length=20000,
        description="Longer opportunity description",
    )
    owner_id: str | None = Field(
        default=None,
        description="Owning user; defaults to creator when omitted (service layer)",
    )
    client_company_id: str | None = Field(
        default=None,
        description="Optional company client UUID (must be client_type=company)",
    )
    contacts: list[LeadContactCreate] | None = Field(
        default=None,
        description="Person clients on the lead; optional labels per association",
    )
    deal_type: DealType | None = Field(
        default=None,
        description="New vs existing business; omit or null when unknown",
    )
    priority: Priority | None = Field(default=None, description="Priority tier")
    notes: list[LeadNoteItem] = Field(default_factory=list, description="Structured notes")
    custom_fields: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Root FieldCell create: field_id plus exactly one of value | sub_fields | items. "
            "Do not send instance_id or type."
        ),
    )

    @field_validator("lead_source", "referral_source", "lead_score", "description")
    @classmethod
    def normalize_blank_strings(cls, value: str | None) -> str | None:
        """Strip whitespace; treat blank strings as unset (``None``)."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class UpdateLeadRequest(BaseModel):
    """Request body for ``PATCH /leads/{lead_id}``.

    Omitted fields are left unchanged; explicit ``null`` clears nullable fields.
    ``notes`` replaces the full array when set (not ``UNSET``).
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str | None | Unset = Field(default=UNSET, description="Lead title; null clears")
    stage_id: str | None | Unset = Field(
        default=UNSET,
        description="Pipeline stage UUID; null clears",
    )
    lead_source: str | None | Unset = Field(
        default=UNSET,
        description="Origin channel; null clears",
    )
    referral_source: str | None | Unset = Field(
        default=UNSET,
        description="Referrer; null clears",
    )
    lead_score: str | None | Unset = Field(
        default=UNSET,
        description="Score label; null clears",
    )
    close_date: date | None | Unset = Field(
        default=UNSET,
        description="Expected close date; null clears",
    )
    amount: Decimal | None | Unset = Field(
        default=UNSET,
        description="Deal value; null clears",
    )
    description: str | None | Unset = Field(
        default=UNSET,
        description="Description; null clears",
    )
    owner_id: str | None | Unset = Field(
        default=UNSET,
        description="Owner user UUID; null unassigns",
    )
    client_company_id: str | None | Unset = Field(
        default=UNSET,
        description="Company client UUID; null clears association",
    )
    deal_type: DealType | None | Unset = Field(default=UNSET, description="Deal type; null clears")
    priority: Priority | None | Unset = Field(default=UNSET, description="Priority; null clears")
    notes: list[LeadNoteItem] | None | Unset = Field(
        default=UNSET,
        description="Replace entire notes array when set",
    )
    contacts: list[LeadContactCreate] | None | Unset = Field(
        default=UNSET,
        description=(
            "Replace entire lead_contacts array when set; omit key leaves contacts unchanged; "
            "null or empty list clears all contacts."
        ),
    )
    custom_fields: list[dict[str, Any]] | Unset = Field(
        default=UNSET,
        description=(
            """FieldCell PATCH: root entries use field_id plus value | sub_fields | items
            (instance_id required for existing roots; list ``items`` is authoritative).
            Nested cells may use instance_id only (optional field_id must match).
            Do not send type."""
        ),
    )

    @field_validator(
        "name",
        "lead_source",
        "referral_source",
        "lead_score",
        "description",
        mode="before",
    )
    @classmethod
    def normalize_blank_strings(cls, value: Any) -> Any:
        """Strip whitespace; treat blank strings as unset (``None``); leave ``UNSET`` unchanged."""
        if value is UNSET or value is None:
            return value
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "UpdateLeadRequest":
        """Raise ValidationException if no fields are set."""
        if not self.model_fields_set:
            raise ValidationException(
                message_key="leads.errors.empty_update_payload",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class LeadsListQueryParams(BaseModel):
    """Validated query string for ``GET /leads``."""

    model_config = ConfigDict(extra="forbid")

    mode: LeadsListMode = Field(
        ...,
        description="list (flat paginated) or kanban (grouped by stage)",
    )
    stage_id: str | None = Field(default=None, description="Filter by pipeline stage")
    search: str | None = Field(
        default=None, description="Search by lead name, company name, or any linked contact name"
    )
    page: int = Field(default=1, ge=1, description="Page number (list mode)")
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Page size (list mode)",
    )

    @field_validator("search")
    @classmethod
    def normalize_search(cls, value: str | None) -> str | None:
        """Strip whitespace; treat blank strings as unset (``None``)."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class LeadListItem(BaseModel):
    """One lead row for list responses and kanban lead arrays."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Lead UUID")
    client_company_id: str | None = Field(None, description="Linked company client UUID")
    company_name: str = Field("", description="Resolved company display name")
    name: str | None = Field(None, description="Lead title")
    stage_id: str | None = Field(None, description="Current stage UUID")
    stage_name: str | None = Field(None, description="Resolved stage display name")
    deal_type: str | None = Field(None, description="Deal type (enum value)")
    priority: str | None = Field(None, description="Priority (enum value)")
    lead_score: str | None = Field(None, description="Score label")
    close_date: date | None = Field(None, description="Expected close date")
    amount: Decimal | None = Field(None, description="Estimated value")
    owner_id: str | None = Field(None, description="Owning organization member user UUID")
    owner_name: str | None = Field(
        None,
        description="Owner display name from auth.users (raw_user_meta_data first/last name)",
    )
    created_at: str = Field(..., description="Created at (ISO 8601)")
    updated_at: str = Field(..., description="Updated at (ISO 8601)")


class LeadKanbanStageGroup(BaseModel):
    """One pipeline column in the kanban ``GET /leads`` response."""

    stage_id: str | None = Field(
        default=None,
        description="Stage UUID; null for leads with no stage assigned",
    )
    stage_name: str = Field(..., description="Stage display name")
    sort_order: int = Field(..., ge=1, description="Stage order in pipeline")
    total: int = Field(..., ge=0, description="Lead count in this column")
    leads: list[LeadListItem] = Field(
        default_factory=list,
        description="Leads in this stage",
    )


class LeadContactDetail(BaseModel):
    """Contact row for ``GET /leads/{id}`` (from ``lead_contacts``)."""

    model_config = ConfigDict(from_attributes=True)

    contact_client_id: str = Field(..., description="Person client UUID")
    label: str | None = Field(None, description="Optional role or tag for this link")
    contact_name: str | None = Field(None, description="Resolved person display name")
    email: str | None = Field(None, description="Email address")
    phones: list[Phone] = Field(default_factory=list, description="Phone numbers")


class LeadDetail(BaseModel):
    """Full lead payload for ``GET /leads/{lead_id}`` (v2)."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Lead UUID")
    client_company_id: str | None = Field(None, description="Linked company client UUID")
    company_name: str = Field("", description="Resolved company display name")
    name: str | None = Field(None, description="Lead title")
    stage_id: str | None = Field(None, description="Current stage UUID")
    stage_name: str | None = Field(None, description="Resolved stage display name")
    deal_type: str | None = Field(None, description="Deal type (enum value)")
    priority: str | None = Field(None, description="Priority (enum value)")
    lead_source: str | None = Field(None, description="Origin channel")
    referral_source: str | None = Field(None, description="Referrer")
    lead_score: str | None = Field(None, description="Score label")
    close_date: date | None = Field(None, description="Expected close date")
    notes: list[LeadNoteItem] = Field(default_factory=list, description="Structured notes")
    amount: Decimal | None = Field(None, description="Estimated value")
    description: str | None = Field(None, description="Opportunity description")
    owner_id: str | None = Field(None, description="Owning organization member user UUID")
    owner_name: str | None = Field(
        None,
        description="Owner display name from auth.users (raw_user_meta_data first/last name)",
    )
    contacts: list[LeadContactDetail] = Field(
        default_factory=list,
        description="Person clients linked via lead_contacts",
    )
    custom_fields: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Resolved FieldCells: field_id, instance_id, type, field_key, label,"
            "and value | sub_fields | items"
        ),
    )
    created_at: str = Field(..., description="Created at (ISO 8601)")
    updated_at: str = Field(..., description="Updated at (ISO 8601)")


__all__ = [
    "LeadsListMode",
    "CreateLeadRequest",
    "UpdateLeadRequest",
    "LeadsListQueryParams",
    "LeadListItem",
    "LeadKanbanStageGroup",
    "LeadDetail",
    "LeadNoteItem",
    "LeadContactCreate",
    "LeadContactDetail",
]
