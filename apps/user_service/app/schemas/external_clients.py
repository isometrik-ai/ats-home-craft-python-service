"""Schemas for external integrations client endpoints.

These schemas exist purely at the API boundary for `/integrations/clients/*`.
They intentionally expose *separate* PATCH payloads for companies vs contacts
while still mapping to the existing internal `UpdateClientRequest` expected by
`ClientService.update_client(...)`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.clients import (
    Address,
    AddressesUpdate,
    BillingPreferences,
    BillingPreferencesUpdate,
    ClientAddressResponse,
    CompanyContact,
    CreateClientRequest,
    EducationalHistoryItem,
    EducationalHistoryUpdate,
    KeyPeopleUpdate,
    KeyPerson,
    LeadInfo,
    LeadManagement,
    LeadManagementUpdate,
    LinkedPageItem,
    LinkedPagesUpdate,
    Phone,
    PhoneInput,
    PrimaryContactInfo,
    PrimaryContactUpdate,
    Product,
    ProductsUpdate,
    SocialPage,
    SocialPagesUpdate,
    UpdateClientRequest,
    Website,
    WebsitesUpdate,
    WorkHistoryItem,
    WorkHistoryUpdate,
)
from apps.user_service.app.schemas.enums import ClientStatus, ClientType


class ExternalCreateContactRequest(BaseModel):
    """External create payload for a contact/person client."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., description="Email address")
    phones: list[PhoneInput] = Field(default_factory=list, max_length=20)

    # Person fields
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    prefix: str | None = Field(None, max_length=10, description="Salutation / prefix")
    middle_name: str | None = Field(None, max_length=100)
    title: str | None = Field(None, max_length=100)
    date_of_birth: date | None = None

    # Link to company (existing or create via name)
    client_company_id: str | None = None
    company_name: str | None = Field(
        None, max_length=200, description="Company name to link/create"
    )

    # Common fields
    profile_photo_url: str | None = Field(None, max_length=500)
    websites: list[Website] = Field(default_factory=list, max_length=10)
    addresses: list[Address] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=50)
    lead_management: LeadManagement | None = Field(
        None,
        description="Optional v2 lead on create (``public.leads`` + ``lead_contacts``)",
    )
    billing_preferences: BillingPreferences | None = None
    custom_fields: list[dict[str, Any]] = Field(default_factory=list)
    portal_access: bool | None = None
    additional_data: dict[str, Any] = Field(default_factory=dict)
    social_pages: list[SocialPage] = Field(default_factory=list, max_length=20)

    def to_create_client_request(self) -> CreateClientRequest:
        """Map external payload into the internal service CREATE payload."""
        data = self.model_dump()
        portal_access = data.pop("portal_access", None)
        if portal_access is None:
            # Let CreateClientRequest default decide when not provided
            pass
        return CreateClientRequest(
            client_type=ClientType.PERSON,
            **{k: v for k, v in data.items() if k != "portal_access"},
            **({"portal_access": portal_access} if portal_access is not None else {}),
        )


class ExternalCreateCompanyPrimaryContact(BaseModel):
    """Primary contact details for creating a company externally."""

    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    prefix: str | None = Field(None, max_length=10)
    middle_name: str | None = Field(None, max_length=100)
    title: str | None = Field(None, max_length=100)
    date_of_birth: date | None = None
    phones: list[PhoneInput] = Field(default_factory=list, max_length=20)


class ExternalCreateCompanyRequest(BaseModel):
    """External create payload for a company client (includes its primary contact)."""

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(..., max_length=200)
    industry: str | None = Field(None, max_length=100)

    # Primary contact (required by internal CreateClientRequest)
    primary_contact: ExternalCreateCompanyPrimaryContact
    email: str = Field(..., description="Primary contact email address")

    # Common fields
    profile_photo_url: str | None = Field(None, max_length=500)
    websites: list[Website] = Field(default_factory=list, max_length=10)
    addresses: list[Address] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=50)
    lead_management: LeadManagement | None = Field(
        None,
        description="Optional v2 lead on create (``public.leads`` + ``lead_contacts``)",
    )
    billing_preferences: BillingPreferences | None = None
    custom_fields: list[dict[str, Any]] = Field(default_factory=list)
    portal_access: bool | None = None
    additional_data: dict[str, Any] = Field(default_factory=dict)
    social_pages: list[SocialPage] = Field(default_factory=list, max_length=20)

    def to_create_client_request(self) -> CreateClientRequest:
        """Map external payload into the internal service CREATE payload."""
        base = self.model_dump(exclude={"primary_contact"})
        primary = self.primary_contact.model_dump()
        portal_access = base.pop("portal_access", None)
        return CreateClientRequest(
            client_type=ClientType.COMPANY,
            email=base["email"],
            phones=primary.get("phones") or [],
            first_name=primary["first_name"],
            last_name=primary["last_name"],
            prefix=primary.get("prefix"),
            middle_name=primary.get("middle_name"),
            title=primary.get("title"),
            date_of_birth=primary.get("date_of_birth"),
            name=base["company_name"],
            industry=base.get("industry"),
            profile_photo_url=base.get("profile_photo_url"),
            websites=base.get("websites") or [],
            addresses=base.get("addresses") or [],
            tags=base.get("tags") or [],
            lead_management=base.get("lead_management"),
            billing_preferences=base.get("billing_preferences"),
            custom_fields=base.get("custom_fields") or [],
            additional_data=base.get("additional_data") or {},
            social_pages=base.get("social_pages") or [],
            **({"portal_access": portal_access} if portal_access is not None else {}),
        )


__all__ = [
    "ExternalCreateCompanyRequest",
    "ExternalCreateContactRequest",
    "ExternalCreateCompanyResult",
    "ExternalCreateContactResult",
    "ExternalCompanyListItem",
    "ExternalContactListItem",
    "ExternalCompanyDetailsResponse",
    "ExternalContactDetailsResponse",
    "ExternalUpdateCompanyRequest",
    "ExternalUpdateContactRequest",
]


class ExternalCreateCompanyResult(BaseModel):
    """External create response for company create."""

    model_config = ConfigDict(extra="forbid")

    company_id: str
    contact_id: str
    lead_id: str | None = None


class ExternalCreateContactResult(BaseModel):
    """External create response for contact create."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str
    company_id: str | None = None
    lead_id: str | None = None


class ExternalPrimaryContactSummary(BaseModel):
    """Slim primary contact summary for external responses."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    email: str | None = None
    phones: list[Phone] = Field(default_factory=list)


class ExternalCompanyListItem(BaseModel):
    """External list item for company clients."""

    model_config = ConfigDict(extra="forbid")

    id: str
    company_name: str = Field(..., description="Company name")
    status: ClientStatus
    industry: str | None = None
    tags: list[str] = Field(default_factory=list)
    image_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    primary_contact: ExternalPrimaryContactSummary | None = None


class ExternalContactListItem(BaseModel):
    """External list item for contact/person clients."""

    model_config = ConfigDict(extra="forbid")

    id: str
    full_name: str = Field(..., description="Contact full name")
    status: ClientStatus
    company_id: str | None = None
    company_name: str | None = None
    email: str | None = None
    phones: list[Phone] = Field(default_factory=list)
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    image_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    is_primary_contact: bool | None = None


class ExternalCompanyDetailsResponse(BaseModel):
    """External details response for a company client."""

    model_config = ConfigDict(extra="forbid")

    id: str
    organization_id: str
    client_type: ClientType
    company_name: str
    status: ClientStatus
    portal_access: bool
    industry: str | None = None
    image_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    primary_contact: PrimaryContactInfo
    company_contacts: list[CompanyContact] = Field(default_factory=list)
    websites: list[Website] = Field(default_factory=list)
    billing_preferences: BillingPreferences | None = None
    custom_fields: list[dict[str, Any]] = Field(default_factory=list)
    addresses: list[ClientAddressResponse] = Field(default_factory=list)
    leads: list[LeadInfo] = Field(
        default_factory=list,
        description=(
            "Leads linked to this client (company and/or contact), newest first by "
            "``updated_at``. Use the first item as the primary pipeline snapshot when only "
            "one lead exists."
        ),
    )
    additional_data: dict[str, Any] = Field(default_factory=dict)
    sales_intelligence: dict[str, Any] = Field(default_factory=dict)
    social_pages: list[SocialPage] = Field(default_factory=list)
    target_market_segments: list[str] = Field(default_factory=list)
    current_tech_stack: list[str] = Field(default_factory=list)
    description: str | None = None
    preferred_communication_channels: list[str] = Field(default_factory=list)
    industry_specific_terminologies: list[str] = Field(default_factory=list)
    linked_pages: list[LinkedPageItem] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)
    key_people: list[KeyPerson] = Field(default_factory=list)
    enrichment_done: bool
    last_enriched_at: str | None = None
    created_at: str
    updated_at: str


class ExternalContactDetailsResponse(BaseModel):
    """External details response for a contact/person client."""

    model_config = ConfigDict(extra="forbid")

    id: str
    organization_id: str
    client_type: ClientType
    full_name: str
    status: ClientStatus
    portal_access: bool
    company_id: str | None = None
    company_name: str | None = None
    image_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    primary_contact: PrimaryContactInfo
    websites: list[Website] = Field(default_factory=list)
    billing_preferences: BillingPreferences | None = None
    custom_fields: list[dict[str, Any]] = Field(default_factory=list)
    addresses: list[ClientAddressResponse] = Field(default_factory=list)
    leads: list[LeadInfo] = Field(
        default_factory=list,
        description=(
            "Leads linked to this client (company and/or contact), newest first by "
            "``updated_at``. Use the first item as the primary pipeline snapshot when only "
            "one lead exists."
        ),
    )
    additional_data: dict[str, Any] = Field(default_factory=dict)
    sales_intelligence: dict[str, Any] = Field(default_factory=dict)
    social_pages: list[SocialPage] = Field(default_factory=list)
    work_history: list[WorkHistoryItem] = Field(default_factory=list)
    educational_history: list[EducationalHistoryItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    enrichment_done: bool
    last_enriched_at: str | None = None
    created_at: str
    updated_at: str


class ExternalUpdateCompanyRequest(BaseModel):
    """External PATCH payload for company-type clients.

    Only includes fields that are meaningful for company clients.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str | None = Field(
        None,
        max_length=200,
        description="Company name (maps to internal client.name)",
    )
    industry: str | None = Field(None, max_length=100)
    profile_photo_url: str | None = Field(None, max_length=500)
    portal_access: bool | None = None
    tags: list[str] | None = Field(None, max_length=50)

    websites: WebsitesUpdate | None = None
    addresses: AddressesUpdate | None = None
    social_pages: SocialPagesUpdate | None = None

    target_market_segments: list[str] | None = Field(None, max_length=50)
    current_tech_stack: list[str] | None = Field(None, max_length=50)
    description: str | None = Field(None, max_length=10000)
    preferred_communication_channels: list[str] | None = Field(None, max_length=20)
    industry_specific_terminologies: list[str] | None = Field(None, max_length=100)
    linked_pages: LinkedPagesUpdate | None = None
    products: ProductsUpdate | None = None
    key_people: KeyPeopleUpdate | None = None

    lead_management: LeadManagementUpdate | None = Field(
        None,
        description="Patch an existing lead by ``lead_id`` (v2 ``public.leads`` columns)",
    )
    billing_preferences: BillingPreferencesUpdate | None = None
    custom_fields: list[dict[str, Any]] | None = None
    additional_data: dict[str, Any] | None = None

    enrichment_done: bool | None = None
    last_enriched_at: datetime | None = None

    primary_contact: PrimaryContactUpdate | None = None

    def to_update_client_request(self) -> UpdateClientRequest:
        """Map external payload into the internal service PATCH payload."""
        return UpdateClientRequest(**self.model_dump(exclude_none=True))


class ExternalUpdateContactRequest(BaseModel):
    """External PATCH payload for person/contact-type clients.

    Only includes fields that are meaningful for person clients.
    """

    model_config = ConfigDict(extra="forbid")

    # Linking / primary flags for contacts under a company
    client_company_id: str | None = Field(
        None,
        description="Linked company client ID for this contact",
    )
    is_primary_contact: bool | None = None

    # Contact -> company linking via name (service may create a new company and link)
    company_name: str | None = Field(
        None,
        max_length=200,
        description="Company name to create/link for this contact (optional)",
    )

    primary_contact: PrimaryContactUpdate | None = None
    profile_photo_url: str | None = Field(None, max_length=500)
    portal_access: bool | None = None
    tags: list[str] | None = Field(None, max_length=50)

    websites: WebsitesUpdate | None = None
    addresses: AddressesUpdate | None = None
    social_pages: SocialPagesUpdate | None = None

    work_history: WorkHistoryUpdate | None = None
    educational_history: EducationalHistoryUpdate | None = None
    skills: list[str] | None = Field(None, max_length=100)

    lead_management: LeadManagementUpdate | None = Field(
        None,
        description="Patch an existing lead by ``lead_id`` (v2 ``public.leads`` columns)",
    )
    billing_preferences: BillingPreferencesUpdate | None = None
    custom_fields: list[dict[str, Any]] | None = None
    additional_data: dict[str, Any] | None = None

    enrichment_done: bool | None = None
    last_enriched_at: datetime | None = None

    def to_update_client_request(self) -> UpdateClientRequest:
        """Map external payload into the internal service PATCH payload."""
        return UpdateClientRequest(**self.model_dump(exclude_none=True))
