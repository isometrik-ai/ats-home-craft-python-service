"""Companies v2 schemas.

These DTOs match the split schema:
- `companies` is the company record
- primary contact is `companies.primary_contact_id` (nullable FK -> contacts)
- memberships are via `contact_companies`
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.clients import (
    AddressInput,
    AddressesUpdate,
    BillingPreferences,
    BillingPreferencesUpdate,
    SocialPage,
    SocialPagesUpdate,
    Website,
    WebsitesUpdate,
)
from apps.user_service.app.schemas.enums import ClientStatus
from apps.user_service.app.schemas.contacts_v2 import CreateContactRequest


class CompanyContactLink(BaseModel):
    """Optional contact association during company create.

    Supports:
    - link an existing contact
    - create a new contact inline and link it
    - optionally set as company primary contact
    """

    model_config = ConfigDict(extra="forbid")

    contact_id: str | None = Field(None, description="Existing contact id to link to the company")
    contact: CreateContactRequest | None = Field(
        None,
        description="Create a new contact inline and link it to the company.",
    )
    is_primary: bool = Field(
        default=False,
        description="If true, set this contact as the company's primary contact.",
    )

    @model_validator(mode="after")
    def validate_exactly_one(self) -> "CompanyContactLink":
        if bool(self.contact_id) == bool(self.contact):
            raise ValueError("Provide exactly one of contact_id or contact.")
        return self


class CreateCompanyRequest(BaseModel):
    """Create a company.

    Supports ADR operations:
    - company only
    - company + link existing contact (primary/non-primary)
    - company + create new contact + link (primary/non-primary)
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., max_length=200)
    industry: str | None = Field(None, max_length=100)
    profile_photo_url: str | None = Field(None, max_length=500)
    portal_access: bool = False

    tags: list[str] = Field(default_factory=list, max_length=50)
    websites: list[Website] = Field(default_factory=list, max_length=10)
    billing_preferences: BillingPreferences | None = None
    social_pages: list[SocialPage] = Field(default_factory=list, max_length=20)

    target_market_segments: list[str] = Field(default_factory=list, max_length=50)
    current_tech_stack: list[str] = Field(default_factory=list, max_length=50)
    preferred_communication_channels: list[str] = Field(default_factory=list, max_length=20)
    industry_specific_terminologies: list[str] = Field(default_factory=list, max_length=100)
    description: str | None = Field(None, max_length=10000)

    custom_fields: list[dict[str, Any]] = Field(default_factory=list)
    additional_data: dict[str, Any] = Field(default_factory=dict)

    # New, developer-friendly association input (one contact on create).
    contact: CompanyContactLink | None = None

    addresses: list[AddressInput] = Field(default_factory=list, max_length=50)


class UpdateCompanyRequest(BaseModel):
    """Patch a company (companies table) and/or manage primary contact."""

    model_config = ConfigDict(extra="forbid")

    status: ClientStatus | None = None
    name: str | None = Field(None, max_length=200)
    industry: str | None = Field(None, max_length=100)
    profile_photo_url: str | None = Field(None, max_length=500)
    portal_access: bool | None = None
    tags: list[str] | None = Field(None, max_length=50)

    websites: WebsitesUpdate | None = None
    billing_preferences: BillingPreferencesUpdate | None = None
    social_pages: SocialPagesUpdate | None = None
    addresses: AddressesUpdate | None = None

    target_market_segments: list[str] | None = None
    current_tech_stack: list[str] | None = None
    preferred_communication_channels: list[str] | None = None
    industry_specific_terminologies: list[str] | None = None
    description: str | None = None

    custom_fields: list[dict[str, Any]] | None = None
    additional_data: dict[str, Any] | None = None

    # optional primary-contact change (ADR section 4)
    primary_contact: CompanyPrimaryContactChange | None = None


class CompanyPrimaryContactChange(BaseModel):
    """Update company.primary_contact_id per ADR."""

    model_config = ConfigDict(extra="forbid")

    # set to an existing contact (must already be member)
    contact_id: str | None = None

    # create a new contact and set as primary
    contact: CreateContactRequest | None = Field(
        default=None
    )

    # unset primary
    unset: bool = False

    @model_validator(mode="after")
    def validate_primary_change(self) -> "CompanyPrimaryContactChange":
        supplied = sum(
            1
            for v in (
                bool(self.contact_id),
                bool(self.contact),
                bool(self.unset),
            )
            if v
        )
        if supplied != 1:
            raise ValueError("Provide exactly one of contact_id, contact, or unset=true.")
        return self


class CompanySummaryResponse(BaseModel):
    """Company list/search item."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    status: ClientStatus
    name: str
    industry: str | None = None
    profile_photo_url: str | None = None
    primary_contact_id: str | None = None
    primary_contact_name: str | None = None
    primary_contact_email: str | None = None
    created_at: str
    updated_at: str


class CompanyDetailsResponse(BaseModel):
    """Company detail response."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    status: ClientStatus
    name: str
    industry: str | None = None
    profile_photo_url: str | None = None
    portal_access: bool = False

    primary_contact_id: str | None = None
    primary_contact: dict[str, Any] | None = None
    contacts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="All contacts in company"
    )

    tags: list[str] = Field(default_factory=list)
    websites: list[dict[str, Any]] = Field(default_factory=list)
    billing_preferences: dict[str, Any] | None = None
    social_pages: list[dict[str, Any]] = Field(default_factory=list)

    custom_fields: list[dict[str, Any]] = Field(default_factory=list)
    additional_data: dict[str, Any] = Field(default_factory=dict)

    target_market_segments: list[str] = Field(default_factory=list)
    current_tech_stack: list[str] = Field(default_factory=list)
    preferred_communication_channels: list[str] = Field(default_factory=list)
    industry_specific_terminologies: list[str] = Field(default_factory=list)
    description: str | None = None

    enrichment_done: bool = False
    enrichment_status: str | None = None
    enrichment_request_id: str | None = None
    last_enriched_at: str | None = None

    addresses: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str

