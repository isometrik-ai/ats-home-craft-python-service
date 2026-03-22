"""Leads Schemas Module.

Pydantic models for lead create, update, list, detail, and query operations.
Aligned with ``public.leads`` and LEADS_API_DOC.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from apps.user_service.app.schemas.enums import LeadsListMode, LeadStatus
from apps.user_service.app.schemas.lead_stages import UNSET, Unset
from apps.user_service.app.utils.common_utils import validate_uuid_format
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

# Maps Pydantic field names to human-readable labels for ``validate_uuid_format``.
_LEAD_UUID_FIELD_LABELS: dict[str, str] = {
    "client_id": "client ID",
    "stage_id": "stage ID",
    "owner_id": "owner ID",
    "point_of_contact": "point of contact ID",
}


class CreateLeadRequest(BaseModel):
    """Request body for ``POST /leads``.

    ``created_by`` and ``organization_id`` are not accepted; enforced via
    ``extra="forbid"``.
    """

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(
        ...,
        description="Existing client UUID (one lead per client)",
    )
    name: str | None = Field(default=None, description="Lead display title")
    stage_id: str | None = Field(
        default=None,
        description=("Pipeline stage; if omitted, backend assigns initial stage when available"),
    )
    lead_status: LeadStatus | None = Field(
        default=None,
        description="Internal status (not exposed in API responses)",
    )
    intake_stage: str | None = Field(
        default=None,
        description="How the lead entered the pipeline (free text)",
    )
    lead_source: str | None = Field(default=None, description="Origin channel")
    referral_source: str | None = Field(
        default=None,
        description="Referrer name or id",
    )
    lead_score: str | None = Field(default=None, description="Score label or tier")
    close_date: date | None = Field(
        default=None,
        description="Expected close date (YYYY-MM-DD)",
    )
    converted_at: datetime | None = Field(
        default=None,
        description="Conversion timestamp (optional on create)",
    )
    notes: str | None = Field(default=None, description="Internal notes")
    amount: Decimal | None = Field(default=None, description="Estimated deal value")
    description: str | None = Field(
        default=None,
        description="Longer opportunity description",
    )
    owner_id: str | None = Field(
        default=None,
        description="Owning user; defaults to creator when omitted (service layer)",
    )
    point_of_contact: str | None = Field(
        default=None,
        description="Primary contact on client side (FK to clients.id per schema)",
    )
    custom_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata",
    )

    @field_validator("client_id", "stage_id", "owner_id", "point_of_contact")
    @classmethod
    def validate_uuid_fields(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        """Validate UUID format for ID fields; skip when the value is omitted."""
        if value is None:
            return None
        label = _LEAD_UUID_FIELD_LABELS[info.field_name]
        validate_uuid_format(value, label)
        return value

    @field_validator(
        "name",
        "intake_stage",
        "lead_source",
        "referral_source",
        "lead_score",
        "notes",
        "description",
    )
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
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str | None | Unset = Field(default=UNSET, description="Lead title; null clears")
    stage_id: str | None | Unset = Field(
        default=UNSET,
        description="Pipeline stage UUID; null clears",
    )
    lead_status: LeadStatus | None | Unset = Field(
        default=UNSET,
        description="Internal status; null clears",
    )
    intake_stage: str | None | Unset = Field(
        default=UNSET,
        description="Intake label; null clears",
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
    converted_at: datetime | None | Unset = Field(
        default=UNSET,
        description="Conversion time; null clears",
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
    point_of_contact: str | None | Unset = Field(
        default=UNSET,
        description="Primary contact UUID; null unassigns",
    )
    notes: str | None | Unset = Field(default=UNSET, description="Notes; null clears")
    custom_fields: dict[str, Any] | Unset = Field(
        default=UNSET,
        description="Merged with stored custom_fields; null values remove keys",
    )

    @field_validator(
        "name",
        "intake_stage",
        "lead_source",
        "referral_source",
        "lead_score",
        "notes",
        "description",
        mode="before",
    )
    @classmethod
    def normalize_update_strings(
        cls,
        value: str | None | Unset,
    ) -> str | None | Unset:
        """Strip string fields before validation; preserve ``UNSET`` and explicit null."""
        if isinstance(value, Unset) or value is None:
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator("stage_id", "owner_id", "point_of_contact", mode="before")
    @classmethod
    def validate_update_uuid_fields(
        cls,
        value: str | None | Unset,
        info: ValidationInfo,
    ) -> str | None | Unset:
        """Validate UUIDs on update when the field is present and not ``UNSET``."""
        if isinstance(value, Unset) or value is None:
            return value
        label = _LEAD_UUID_FIELD_LABELS[info.field_name]
        validate_uuid_format(value, label)
        return value

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> "UpdateLeadRequest":
        """Reject empty PATCH bodies."""
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
    search: str | None = Field(default=None, description="Search by lead name")
    page: int = Field(default=1, ge=1, description="Page number (list mode)")
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Page size (list mode)",
    )

    @field_validator("stage_id")
    @classmethod
    def validate_stage_filter(cls, value: str | None) -> str | None:
        """Validate ``stage_id`` when provided as a filter."""
        if value is None:
            return None
        validate_uuid_format(value, "stage ID")
        return value


class LeadListItem(BaseModel):
    """One lead row for list responses and kanban lead arrays."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Lead UUID")
    client_id: str = Field(..., description="Client UUID")
    client_name: str = Field(..., description="Resolved client display name")
    name: str | None = Field(None, description="Lead title")
    stage_id: str | None = Field(None, description="Current stage UUID")
    stage_name: str | None = Field(None, description="Resolved stage display name")
    lead_score: str | None = Field(None, description="Score label")
    close_date: date | None = Field(None, description="Expected close date")
    amount: Decimal | None = Field(None, description="Estimated value")
    point_of_contact: str | None = Field(None, description="Primary contact UUID")
    created_at: str = Field(..., description="Created at (ISO 8601)")
    updated_at: str = Field(..., description="Updated at (ISO 8601)")


class LeadKanbanStageGroup(BaseModel):
    """One pipeline column in the kanban ``GET /leads`` response."""

    stage_id: str = Field(..., description="Stage UUID")
    stage_name: str = Field(..., description="Stage display name")
    sort_order: int = Field(..., ge=1, description="Stage order in pipeline")
    total: int = Field(..., ge=0, description="Lead count in this column")
    leads: list[LeadListItem] = Field(
        default_factory=list,
        description="Leads in this stage",
    )


class LeadDetail(BaseModel):
    """Full lead payload for ``GET /leads/{lead_id}`` (excludes ``lead_status``)."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Lead UUID")
    client_id: str = Field(..., description="Client UUID")
    client_name: str = Field(..., description="Resolved client display name")
    name: str | None = Field(None, description="Lead title")
    stage_id: str | None = Field(None, description="Current stage UUID")
    stage_name: str | None = Field(None, description="Resolved stage display name")
    intake_stage: str | None = Field(None, description="Intake label")
    lead_source: str | None = Field(None, description="Origin channel")
    referral_source: str | None = Field(None, description="Referrer")
    lead_score: str | None = Field(None, description="Score label")
    close_date: date | None = Field(None, description="Expected close date")
    converted_at: str | None = Field(None, description="Conversion time (ISO 8601)")
    notes: str | None = Field(None, description="Internal notes")
    amount: Decimal | None = Field(None, description="Estimated value")
    created_by: str | None = Field(None, description="User who created the lead")
    description: str | None = Field(None, description="Opportunity description")
    owner_id: str | None = Field(None, description="Owning user UUID")
    point_of_contact: str | None = Field(None, description="Primary contact UUID")
    custom_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Custom metadata",
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
]
