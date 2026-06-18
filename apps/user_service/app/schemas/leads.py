"""Leads Schemas Module.

Pydantic models for lead create, update, list, detail, and query operations.
Aligned with ``public.leads``, ``public.lead_contacts``, and ``public.lead_companies``.
"""

from __future__ import annotations

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

from apps.user_service.app.schemas.common import NoteItem, Phone
from apps.user_service.app.schemas.companies import CreateCompanyRequestStandalone
from apps.user_service.app.schemas.contacts import CreateContactRequestStandalone
from apps.user_service.app.schemas.enums import (
    DealType,
    LeadCurrency,
    LeadsListMode,
    Priority,
)
from apps.user_service.app.schemas.lead_stages import UNSET, Unset
from apps.user_service.app.schemas.list_filters import DropdownCustomFieldFilter
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class LeadNoteItem(NoteItem):
    """One structured note in ``leads.notes`` (JSONB array)."""


class LeadContactCreate(BaseModel):
    """Contact linked to a lead (``lead_contacts``)."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str = Field(..., description="Contact UUID")
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


class LeadCompanyCreate(BaseModel):
    """Company linked to a lead (``lead_companies``)."""

    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(..., description="Company UUID")
    label: str | None = Field(
        default=None,
        max_length=255,
        description="Optional role or tag for this association",
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


class LeadContactAssociationUpdate(BaseModel):
    """Update per-contact attributes for a lead relationship (currently label only)."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str = Field(..., description="Existing contact id to update relationship for")
    label: str | None = Field(
        default=None,
        max_length=255,
        description="Optional role or tag for this association (null clears label)",
    )

    @field_validator("contact_id", mode="before")
    @classmethod
    def normalize_contact_id(cls, value: Any) -> Any:
        """Strip whitespace from contact id when provided as a string."""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value: Any) -> Any:
        """Strip whitespace; treat blank strings as unset (``None``)."""
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class LeadContactsUpdate(BaseModel):
    """Batch contact association changes for a lead (delta updates).

    Supports in one request:
    - remove association with N contacts
    - add association with N existing contacts (optionally setting label per contact)
    - update label for N existing contact relationships without unlinking
    """

    model_config = ConfigDict(extra="forbid")

    remove_associations: list[str] = Field(
        default_factory=list,
        description="Contact ids to unlink from the lead",
    )
    add_associations: list[LeadContactCreate] = Field(
        default_factory=list,
        description="Associate the lead with existing contacts (by id)",
    )
    update_associations: list[LeadContactAssociationUpdate] = Field(
        default_factory=list,
        description="Update label per contact without unlinking",
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "LeadContactsUpdate":
        """Normalize ids and validate at least one delta operation is provided."""
        remove_ids = [c.strip() for c in (self.remove_associations or []) if (c or "").strip()]
        self.remove_associations = remove_ids

        normalized_add: list[LeadContactCreate] = []
        for item in self.add_associations or []:
            cid = (getattr(item, "contact_id", None) or "").strip()
            if not cid:
                raise ValueError("add_associations.contact_id is required.")
            normalized_add.append(
                LeadContactCreate(contact_id=cid, label=getattr(item, "label", None))
            )
        self.add_associations = normalized_add

        normalized_update: list[LeadContactAssociationUpdate] = []
        for item in self.update_associations or []:
            cid = (getattr(item, "contact_id", None) or "").strip()
            if not cid:
                raise ValueError("update_associations.contact_id is required.")
            normalized_update.append(
                LeadContactAssociationUpdate(contact_id=cid, label=getattr(item, "label", None))
            )
        self.update_associations = normalized_update

        if (
            not self.remove_associations
            and not self.add_associations
            and not self.update_associations
        ):
            raise ValueError("Provide at least one operation in contacts_update.")

        return self


class LeadCompanyAssociationUpdate(BaseModel):
    """Update per-company attributes for a lead relationship (currently label only)."""

    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(..., description="Existing company id to update relationship for")
    label: str | None = Field(
        default=None,
        max_length=255,
        description="Optional role or tag for this association (null clears label)",
    )

    @field_validator("company_id", mode="before")
    @classmethod
    def normalize_company_id(cls, value: Any) -> Any:
        """Strip whitespace from company id when provided as a string."""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value: Any) -> Any:
        """Strip whitespace; treat blank strings as unset (``None``)."""
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class LeadCompaniesUpdate(BaseModel):
    """Batch company association changes for a lead (delta updates).

    Supports in one request:
    - remove association with N companies
    - add association with N existing companies (optionally setting label per company)
    - update label for N existing company relationships without unlinking
    """

    model_config = ConfigDict(extra="forbid")

    remove_associations: list[str] = Field(
        default_factory=list,
        description="Company ids to unlink from the lead",
    )
    add_associations: list[LeadCompanyCreate] = Field(
        default_factory=list,
        description="Associate the lead with existing companies (by id)",
    )
    update_associations: list[LeadCompanyAssociationUpdate] = Field(
        default_factory=list,
        description="Update label per company without unlinking",
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "LeadCompaniesUpdate":
        """Normalize ids and validate at least one delta operation is provided."""
        remove_ids = [c.strip() for c in (self.remove_associations or []) if (c or "").strip()]
        self.remove_associations = remove_ids

        normalized_add: list[LeadCompanyCreate] = []
        for item in self.add_associations or []:
            cid = (getattr(item, "company_id", None) or "").strip()
            if not cid:
                raise ValueError("add_associations.company_id is required.")
            normalized_add.append(
                LeadCompanyCreate(company_id=cid, label=getattr(item, "label", None))
            )
        self.add_associations = normalized_add

        normalized_update: list[LeadCompanyAssociationUpdate] = []
        for item in self.update_associations or []:
            cid = (getattr(item, "company_id", None) or "").strip()
            if not cid:
                raise ValueError("update_associations.company_id is required.")
            normalized_update.append(
                LeadCompanyAssociationUpdate(company_id=cid, label=getattr(item, "label", None))
            )
        self.update_associations = normalized_update

        if (
            not self.remove_associations
            and not self.add_associations
            and not self.update_associations
        ):
            raise ValueError("Provide at least one operation in companies_update.")

        return self


class CreateLeadCompany(BaseModel):
    """Optional explicit company on ``POST /leads``."""

    model_config = ConfigDict(extra="forbid")

    company_id: str | None = Field(default=None, description="Company UUID")
    label: str | None = Field(
        default=None,
        max_length=255,
        description="Optional label for the company association on create",
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
    """Request body for ``POST /leads`` (``public.leads`` + junctions)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

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
    currency: LeadCurrency | None = Field(
        default=None,
        description="ISO 4217 alpha-3 currency code for `amount`",
    )
    description: str | None = Field(
        default=None,
        max_length=20000,
        description="Longer opportunity description",
    )
    owner_id: str | None = Field(
        default=None,
        description="Owning user; defaults to creator when omitted (service layer)",
    )
    company: CreateLeadCompany | None = Field(
        default=None,
        description=(
            "Optional company on create (``lead_companies``). "
            "All companies linked to each contact via ``contact_companies`` are associated as well."
        ),
    )
    contacts: list[LeadContactCreate] | None = Field(
        default=None,
        description=(
            "Contacts on the lead; optional labels per association. "
            "Each contact's company memberships are linked to the lead automatically."
        ),
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
    create_contact: CreateContactRequestStandalone | None = Field(
        default=None,
        alias="contact",
    )
    created_contact_label: str | None = Field(
        default=None,
        max_length=255,
        alias="lead_contact_label",
    )
    create_company: CreateCompanyRequestStandalone | None = Field(
        default=None,
        description=(
            "Optional inline company create linked on the lead only (``lead_companies``). "
            "Does not create a ``contact_companies`` row."
        ),
    )
    created_company_label: str | None = Field(
        default=None,
        max_length=255,
        alias="lead_company_label",
        description=(
            "Optional label for the inline-created company on the lead (lead_companies.label)."
        ),
    )

    def to_lead_payload(self) -> "CreateLeadRequest":
        """Lead fields only (omit inline contact/company create inputs)."""
        return self.model_copy(
            update={
                "create_contact": None,
                "created_contact_label": None,
                "create_company": None,
                "created_company_label": None,
            },
            deep=True,
        )

    @model_validator(mode="after")
    def validate_company_link_vs_create(self) -> "CreateLeadRequest":
        """``company.company_id`` and inline ``create_company`` are mutually exclusive."""
        has_link = self.company is not None and (self.company.company_id or "").strip()
        if has_link and self.create_company is not None:
            raise ValidationException(
                message_key="leads.errors.company_link_or_create_only",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self

    @field_validator("lead_source", "referral_source", "lead_score", "description")
    @classmethod
    def normalize_blank_strings(cls, value: str | None) -> str | None:
        """Strip whitespace; treat blank strings as unset (``None``)."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def require_currency_when_amount_present(self) -> "CreateLeadRequest":
        """Currency rules:

        - If `amount` is provided (non-null), `currency` is mandatory.
        - If `amount` is omitted/null, `currency` must not be sent.
        """
        if self.amount is not None:
            if self.currency is None:
                raise ValidationException(
                    message_key="leads.errors.currency_required_when_amount_provided",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            return self

        if self.currency is not None:
            raise ValidationException(
                message_key="leads.errors.currency_not_allowed_without_amount",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


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
    currency: LeadCurrency | None | Unset = Field(
        default=UNSET,
        description="ISO 4217 alpha-3 currency code for amount; null clears",
    )
    description: str | None | Unset = Field(
        default=UNSET,
        description="Description; null clears",
    )
    owner_id: str | None | Unset = Field(
        default=UNSET,
        description="Owner user UUID; null unassigns",
    )
    deal_type: DealType | None | Unset = Field(default=UNSET, description="Deal type; null clears")
    priority: Priority | None | Unset = Field(default=UNSET, description="Priority; null clears")
    notes: list[LeadNoteItem] | None | Unset = Field(
        default=UNSET,
        description="Replace entire notes array when set",
    )
    contacts_update: LeadContactsUpdate | None = Field(
        default=None,
        description=(
            "Delta operations for lead_contacts (add/remove/update labels). "
            "Omit to leave contacts unchanged. "
            "Companies linked to newly added contacts are associated automatically; "
            "unlinking contacts does not remove lead companies."
        ),
    )
    companies_update: LeadCompaniesUpdate | None = Field(
        default=None,
        description=(
            "Delta operations for lead_companies (add/remove/update labels). "
            "Omit to leave companies unchanged."
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
        if "contacts_update" in self.model_fields_set and self.contacts_update is None:
            raise ValueError("contacts_update must be an object when provided.")
        if "companies_update" in self.model_fields_set and self.companies_update is None:
            raise ValueError("companies_update must be an object when provided.")

        amount_set = "amount" in self.model_fields_set and not isinstance(self.amount, Unset)
        currency_set = "currency" in self.model_fields_set and not isinstance(self.currency, Unset)

        # If currency is sent, amount must also be sent and must be non-null.
        if currency_set:
            if not amount_set or self.amount is None:
                raise ValidationException(
                    message_key="leads.errors.currency_not_allowed_without_amount",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

        # If PATCH sets a non-null amount, currency must be sent and non-null.
        if amount_set and self.amount is not None:
            if (not currency_set) or self.currency is None:
                raise ValidationException(
                    message_key="leads.errors.currency_required_when_amount_provided",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )

        if not self.model_fields_set:
            raise ValidationException(
                message_key="leads.errors.empty_update_payload",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class LeadsListQueryParams(BaseModel):
    """Validated filters for listing leads (list or kanban)."""

    model_config = ConfigDict(extra="forbid")

    mode: LeadsListMode = Field(
        ...,
        description="list (flat paginated) or kanban (grouped by stage)",
    )
    stage_id: str | None = Field(default=None, description="Filter by pipeline stage")
    owner_id: str | None = Field(default=None, description="Filter by owner user id")
    start_date: date | None = Field(default=None, description="created_at date (inclusive)")
    end_date: date | None = Field(default=None, description="created_at date (inclusive)")
    search: str | None = Field(
        default=None,
        description="Search by lead name, company name, owner name, contact name, or contact email",
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

    @model_validator(mode="after")
    def validate_date_range(self) -> LeadsListQueryParams:
        """Validate that the start date is before the end date."""
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationException(
                message_key="leads.errors.invalid_date_range",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class ListLeadsRequest(LeadsListQueryParams):
    """Request body for ``POST /leads/list``."""

    dropdown_filters: list[DropdownCustomFieldFilter] = Field(default_factory=list)


class LeadCompanyListItem(BaseModel):
    """Company row on a lead (list/detail)."""

    model_config = ConfigDict(from_attributes=True)

    company_id: str = Field(..., description="Company UUID")
    label: str | None = Field(None, description="Optional role or tag for this link")
    company_name: str = Field("", description="Resolved company display name")
    profile_photo_url: str | None = Field(
        default=None,
        description="Company profile image URL",
    )


class LeadListItem(BaseModel):
    """One lead row for list responses and kanban lead arrays."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Lead UUID")
    companies: list[LeadCompanyListItem] = Field(
        default_factory=list,
        description="Companies linked via lead_companies",
    )
    contacts: list[LeadContactDetail] = Field(
        default_factory=list,
        description="Contacts linked via lead_contacts",
    )
    name: str | None = Field(None, description="Lead title")
    stage_id: str | None = Field(None, description="Current stage UUID")
    stage_name: str | None = Field(None, description="Resolved stage display name")
    deal_type: str | None = Field(None, description="Deal type (enum value)")
    priority: str | None = Field(None, description="Priority (enum value)")
    lead_score: str | None = Field(None, description="Score label")
    close_date: date | None = Field(None, description="Expected close date")
    amount: Decimal | None = Field(None, description="Estimated value")
    currency: str | None = Field(None, description="ISO 4217 alpha-3 currency code for amount")
    owner_id: str | None = Field(None, description="Owning organization member user UUID")
    owner_name: str | None = Field(
        None,
        description="Owner display name from organization_members (first_name / last_name)",
    )
    created_at: str = Field(..., description="Created at (ISO 8601)")
    updated_at: str = Field(..., description="Updated at (ISO 8601)")


class LeadKanbanStageGroup(BaseModel):
    """One pipeline column in the kanban leads list response."""

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

    contact_id: str = Field(..., description="Contact UUID")
    label: str | None = Field(None, description="Optional role or tag for this link")
    contact_name: str | None = Field(None, description="Resolved person display name")
    email: str | None = Field(None, description="Email address")
    phones: list[Phone] = Field(default_factory=list, description="Phone numbers")
    profile_photo_url: str | None = Field(
        default=None,
        description="Contact profile image URL",
    )
    addresses: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Contact addresses (primary first)",
    )


class LeadDetail(BaseModel):
    """Full lead payload for ``GET /leads/{lead_id}``."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Lead UUID")
    companies: list[LeadCompanyListItem] = Field(
        default_factory=list,
        description="Companies linked via lead_companies",
    )
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
    currency: str | None = Field(None, description="ISO 4217 alpha-3 currency code for amount")
    description: str | None = Field(None, description="Opportunity description")
    owner_id: str | None = Field(None, description="Owning organization member user UUID")
    owner_name: str | None = Field(
        None,
        description="Owner display name from organization_members (first_name / last_name)",
    )
    owner_email: str | None = Field(
        None,
        description="Owner email from organization_members",
    )
    contacts: list[LeadContactDetail] = Field(
        default_factory=list,
        description="Contacts linked via lead_contacts",
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
    "CreateLeadCompany",
    "CreateLeadRequest",
    "UpdateLeadRequest",
    "LeadsListQueryParams",
    "ListLeadsRequest",
    "LeadListItem",
    "LeadKanbanStageGroup",
    "LeadDetail",
    "LeadNoteItem",
    "LeadContactCreate",
    "LeadCompanyCreate",
    "LeadContactsUpdate",
    "LeadCompaniesUpdate",
    "LeadCompanyListItem",
    "LeadContactDetail",
]
