"""Client Management Schemas Module.

This module contains Pydantic models for client management operations.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.user_service.app.schemas.common import (
    Address,
    AddressesUpdate,
    BillingPreferences,
    BillingPreferencesUpdate,
    EducationalHistoryItem,
    EducationalHistoryUpdate,
    KeyPeopleUpdate,
    KeyPerson,
    LinkedPageItem,
    LinkedPagesUpdate,
    Phone,
    PhoneInput,
    PhonesUpdate,
    Product,
    ProductsUpdate,
    SocialPage,
    SocialPagesUpdate,
    Website,
    WebsitesUpdate,
    WorkHistoryItem,
    WorkHistoryUpdate,
)
from apps.user_service.app.schemas.enums import (
    AddressType,
    ClientStatus,
    ClientType,
)
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

PORTAL_ACCESS_DEFAULT = False


class LeadManagement(BaseModel):
    """Lead management schema (aligned with v2 ``public.leads`` + ``lead_contacts``)."""

    enabled: bool = Field(default=False, description="Enable lead management")
    stage_id: str | None = Field(
        None, description="Pipeline stage ID for the new lead (required when enabled)"
    )
    lead_source: str | None = Field(None, description="Lead source", max_length=100)
    referral_source: str | None = Field(None, description="Referral source", max_length=200)
    lead_score: str | None = Field(None, description="Lead score")

    @model_validator(mode="after")
    def require_stage_when_enabled(self) -> "LeadManagement":
        """``stage_id`` is required when creating an associated lead."""
        if self.enabled and not (self.stage_id and str(self.stage_id).strip()):
            raise ValidationException(
                message_key="clients.errors.lead_stage_id_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class LeadManagementUpdate(BaseModel):
    """Lead update by lead_id (v2 ``public.leads`` columns only)."""

    lead_id: str = Field(..., description="Lead ID to update")
    enabled: bool | None = None
    stage_id: str | None = Field(None, description="Pipeline stage ID to assign")
    lead_source: str | None = Field(None, max_length=100)
    referral_source: str | None = Field(None, max_length=200)
    lead_score: str | None = None
    notes: str | None = None


class PrimaryContactUpdate(BaseModel):
    """Primary contact PATCH; only provided fields are applied."""

    model_config = ConfigDict(extra="forbid")

    salutation: str | None = Field(None, max_length=100)
    first_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    title: str | None = Field(None, max_length=100)
    company_name: str | None = Field(
        None,
        max_length=200,
        description="Company name (company type only)",
    )
    phones: PhonesUpdate | None = Field(
        None, description="Batch phone operations: add, update, and/or remove"
    )


class UpdateClientRequest(BaseModel):
    """Client PATCH payload; only provided fields are applied.

    List-type fields (websites, addresses, social_pages) support batch operations:
    multiple add, update, and/or remove operations per field.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str | None = Field(
        None,
        max_length=200,
        description="Company name (company type only)",
    )
    client_company_id: str | None = Field(
        None,
        description=(
            "Linked company client ID for contact/person updates "
            "(updates primary contact client_user.client_company_id)"
        ),
    )
    is_primary_contact: bool | None = Field(
        None,
        description=(
            "Set/unset this contact as primary for its linked company. "
            "When true, other contacts under the same company are unmarked."
        ),
    )
    primary_contact: PrimaryContactUpdate | None = None
    industry: str | None = Field(None, max_length=100)
    profile_photo_url: str | None = Field(None, max_length=500)
    portal_access: bool | None = None
    tags: list[str] | None = Field(None, max_length=50)
    websites: WebsitesUpdate | None = Field(
        None, description="Batch website operations: add, update, and/or remove"
    )
    addresses: AddressesUpdate | None = Field(
        None, description="Batch address operations: add, update, and/or remove"
    )
    social_pages: SocialPagesUpdate | None = Field(
        None, description="Batch social page operations: add, update, and/or remove"
    )
    # Individual (person) type only: work_history, educational_history, skills
    work_history: WorkHistoryUpdate | None = Field(
        None, description="Batch work history operations (person type only)"
    )
    educational_history: EducationalHistoryUpdate | None = Field(
        None, description="Batch educational history operations (person type only)"
    )
    skills: list[str] | None = Field(None, max_length=100, description="Skills (person type only)")
    # Company type only: target_market_segments, current_tech_stack, description, etc.
    target_market_segments: list[str] | None = Field(
        None, max_length=50, description="Target market segments (company type only)"
    )
    current_tech_stack: list[str] | None = Field(
        None, max_length=50, description="Current tech stack (company type only)"
    )
    description: str | None = Field(
        None, max_length=10000, description="Description (company type only)"
    )
    preferred_communication_channels: list[str] | None = Field(
        None, max_length=20, description="Preferred communication channels (company type only)"
    )
    industry_specific_terminologies: list[str] | None = Field(
        None, max_length=100, description="Industry-specific terminologies (company type only)"
    )
    linked_pages: LinkedPagesUpdate | None = Field(
        None, description="Batch linked page operations (company type only)"
    )
    products: ProductsUpdate | None = Field(
        None, description="Batch product operations (company type only)"
    )
    key_people: KeyPeopleUpdate | None = Field(
        None, description="Batch key people operations (company type only)"
    )
    lead_management: LeadManagementUpdate | None = None
    billing_preferences: BillingPreferencesUpdate | None = None
    custom_fields: list[dict[str, Any]] | None = Field(
        None,
        description=(
            """FieldCell PATCH: root entries use field_id plus exactly one of value
            | sub_fields | items
            (instance_id required for existing roots; list ``items`` is authoritative).
            Nested cells may be updated with only instance_id plus
            exactly one of value | sub_fields | items
            (optional field_id must match that cell). Do not send type."""
        ),
    )
    additional_data: dict[str, Any] | None = None
    enrichment_done: bool | None = None
    last_enriched_at: datetime | None = None

    @model_validator(mode="after")
    def validate_company_link_and_primary_contact(self) -> "UpdateClientRequest":
        """Disallow passing company-link and primary toggle together."""
        if self.client_company_id is not None and self.is_primary_contact is not None:
            raise ValidationException(
                message_key="clients.errors.client_company_and_primary_contact_mutually_exclusive",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class CreateClientRequest(BaseModel):
    """Request schema for creating a client."""

    client_type: ClientType = Field(..., description="Client type")
    email: str | None = Field(None, description="Email address (required for person/portal access)")
    phones: list[PhoneInput] = Field(
        default_factory=list,
        max_length=20,
        description="Phone numbers for primary contact",
    )

    # Primary contact fields (required for person; optional for company-only create)
    first_name: str | None = Field(None, description="First name", max_length=100)
    last_name: str | None = Field(None, description="Last name", max_length=100)

    # Person type fields
    prefix: str | None = Field(None, description="Name prefix", max_length=10)
    middle_name: str | None = Field(None, description="Middle name", max_length=100)
    company_name: str | None = Field(None, description="Company name", max_length=200)
    title: str | None = Field(None, description="Job title", max_length=100)
    date_of_birth: date | None = Field(None, description="Date of birth")
    client_company_id: str | None = Field(
        None,
        description=(
            "Linked company client ID when client_type is 'person' "
            "(used to associate the person with a company client)"
        ),
    )

    # Company type fields
    name: str | None = Field(None, description="Company name", max_length=200)
    industry: str | None = Field(None, description="Industry", max_length=100)
    primary_contact_id: str | None = Field(
        None,
        description=(
            "Existing client_user id to link as company primary contact "
            "(company create only; sets client_users.client_company_id)"
        ),
    )

    # Common fields
    profile_photo_url: str | None = Field(None, description="Profile photo URL", max_length=500)
    websites: list[Website] = Field(default_factory=list, description="Websites", max_length=10)
    addresses: list[Address] = Field(default_factory=list, description="Addresses", max_length=10)
    tags: list[str] = Field(default_factory=list, description="Tags", max_length=50)
    lead_management: LeadManagement | None = Field(None, description="Lead management")
    billing_preferences: BillingPreferences | None = Field(None, description="Billing preferences")
    custom_fields: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            """Root FieldCell create payload:
            field_id plus exactly one of value | sub_fields | items. "
            Do not send instance_id or type."""
        ),
    )
    portal_access: bool = Field(
        default=PORTAL_ACCESS_DEFAULT, description="Portal access enabled flag"
    )
    additional_data: dict[str, Any] = Field(
        default_factory=dict, description="Dynamic data stored as passed"
    )
    social_pages: list[SocialPage] = Field(
        default_factory=list, description="Social platform and URL entries", max_length=20
    )

    @field_validator("websites")
    @classmethod
    def validate_primary_website(cls, websites: list[Website]) -> list[Website]:
        """Validate only one primary website."""
        primary_count = sum(1 for w in websites if w.is_primary)
        if primary_count > 1:
            raise ValidationException(
                message_key="clients.errors.only_one_primary_website",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return websites

    @field_validator("addresses")
    @classmethod
    def validate_primary_address(cls, addresses: list[Address]) -> list[Address]:
        """Validate only one primary address."""
        primary_count = sum(1 for a in addresses if a.is_primary)
        if primary_count > 1:
            raise ValidationException(
                message_key="clients.errors.only_one_primary_address",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return addresses

    @field_validator("name")
    @classmethod
    def validate_company_name(cls, company_name: str | None, info) -> str | None:
        """Validate company type required fields."""
        if info.data.get("client_type") == ClientType.COMPANY:
            if not company_name:
                raise ValidationException(
                    message_key="clients.errors.company_name_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
        return company_name

    @field_validator("phones")
    @classmethod
    def validate_primary_phone_create(cls, phones: list[PhoneInput]) -> list[PhoneInput]:
        """At most one primary phone on create."""
        if sum(1 for p in phones if p.is_primary) > 1:
            raise ValidationException(
                message_key="clients.errors.only_one_primary_phone",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return phones

    @model_validator(mode="after")
    def validate_required_fields_by_type(self) -> "CreateClientRequest":
        """Validate required fields based on client_type and primary-contact intent.

        - PERSON: requires email, first_name, last_name
        - COMPANY:
          - always requires company name (validated separately)
          - allows company-only create with no primary-contact fields
          - if any primary-contact fields are provided (or portal access is enabled),
            require email + first_name + last_name.
        """
        is_person = self.client_type == ClientType.PERSON
        is_company = self.client_type == ClientType.COMPANY

        if is_person:
            if not self.email:
                raise ValidationException(
                    message_key="clients.errors.email_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            if not self.first_name or not self.last_name:
                raise ValidationException(
                    message_key="clients.errors.first_last_name_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            return self

        if is_company:
            if self.primary_contact_id and self.client_type != ClientType.COMPANY:
                raise ValidationException(
                    message_key="clients.errors.person_fields_not_allowed_for_company",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            primary_contact_intended = any(
                [
                    bool(self.primary_contact_id),
                    bool(self.email),
                    bool(self.first_name),
                    bool(self.last_name),
                    bool(self.phones),
                    bool(self.portal_access),
                ]
            )
            if primary_contact_intended:
                # If the caller is linking an existing primary contact, do not require
                # creating a new auth user for it.
                if self.primary_contact_id:
                    return self
                if not self.email:
                    raise ValidationException(
                        message_key="clients.errors.email_required",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                    )
                if not self.first_name or not self.last_name:
                    raise ValidationException(
                        message_key="clients.errors.first_last_name_required",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                    )
            return self

        return self


class CreateClientFromUserRequest(BaseModel):
    """Request schema for creating a client from user ID."""

    user_id: str = Field(..., description="User ID from auth.users table")
    organization_id: str = Field(..., description="Organization ID")


class PrimaryContactInfo(BaseModel):
    """Primary contact information schema."""

    salutation: str | None = Field(None, description="Salutation / prefix (Mr., Ms., etc.)")
    first_name: str | None = Field(None, description="First name")
    middle_name: str | None = Field(None, description="Middle name")
    last_name: str | None = Field(None, description="Last name")
    title: str | None = Field(None, description="Job title")
    email: str | None = Field(None, description="Email address")
    phones: list[Phone] = Field(default_factory=list, description="Phone numbers")


class ClientListResponse(BaseModel):
    """Client list response schema."""

    id: str = Field(..., description="Client ID")
    name: str = Field(..., description="Client name")
    company_id: str | None = Field(
        None,
        description="Linked company client ID when client is a person",
    )
    company_name: str | None = Field(
        None, description="Linked company name when client is a person"
    )
    primary_contact: PrimaryContactInfo = Field(..., description="Primary contact information")
    client_type: ClientType = Field(..., description="Client type")
    status: ClientStatus = Field(..., description="Client status")
    industry: str | None = Field(None, description="Industry", max_length=100)
    projects: list[Any] = Field(default_factory=list, description="Projects list")
    image_url: str | None = Field(None, description="Primary profile image URL")
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Update timestamp")
    outstanding: None = Field(None, description="Outstanding balance")
    tags: list[str] = Field(default_factory=list, description="Tags")


class ClientAddressResponse(BaseModel):
    """Client address response schema."""

    id: str = Field(..., description="Address ID")
    place_id: str | None = Field(None, description="Place ID")
    address_line1: str = Field(..., description="Primary address line")
    address_line2: str | None = Field(None, description="Secondary address line")
    city: str | None = Field(None, description="City")
    state: str | None = Field(None, description="State/Province")
    postal_code: str | None = Field(None, description="Postal/ZIP code")
    country: str | None = Field(None, description="Country")
    latitude: float | None = Field(None, description="Latitude coordinate")
    longitude: float | None = Field(None, description="Longitude coordinate")
    address_type: AddressType | None = Field(None, description="Address type")
    address_data: dict[str, Any] = Field(
        default_factory=dict, description="Additional address metadata"
    )
    is_primary: bool = Field(default=False, description="Primary address flag")
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Update timestamp")


class LeadInfo(BaseModel):
    """Lead summary on client detail (subset aligned with lead list/detail)."""

    id: str = Field(..., description="Lead ID")
    name: str = Field(..., description="Lead title")
    stage_id: str | None = Field(None, description="Pipeline stage ID")
    stage_name: str | None = Field(None, description="Stage display name")
    deal_type: str | None = Field(None, description="Deal type (enum value)")
    priority: str | None = Field(None, description="Priority (enum value)")
    lead_score: str | None = Field(None, description="Score label")
    close_date: date | None = Field(None, description="Expected close date")
    amount: Decimal | None = Field(None, description="Estimated deal value")
    owner_id: str | None = Field(None, description="Owning user UUID")
    owner_name: str | None = Field(None, description="Owner display name")
    lead_source: str | None = Field(None, description="Origin channel")
    referral_source: str | None = Field(None, description="Referrer")
    created_at: str | None = Field(None, description="Created at (ISO 8601)")
    updated_at: str | None = Field(None, description="Updated at (ISO 8601)")


class CompanyContact(BaseModel):
    """Contact linked to a company client (for All Contacts section)."""

    name: str | None = Field(None, description="Full name of the contact")
    designation: str | None = Field(None, description="Job title/designation")
    email: str | None = Field(None, description="Email address")
    is_primary_contact: bool = Field(
        default=False, description="Whether this contact is the primary contact for the company"
    )


class ClientDetailsResponse(BaseModel):
    """Client details response schema."""

    id: str = Field(..., description="Client ID")
    organization_id: str = Field(..., description="Organization ID")
    client_type: ClientType = Field(..., description="Client type")
    name: str = Field(..., description="Client name")
    company_id: str | None = Field(
        None,
        description="Linked company client ID when client is a person and client_company_id is set",
    )
    company_name: str | None = Field(
        None,
        description="Linked company name when client is a person and client_company_id is set",
    )
    status: ClientStatus = Field(..., description="Client status")
    portal_access: bool = Field(
        default=PORTAL_ACCESS_DEFAULT, description="Portal access enabled flag"
    )
    industry: str | None = Field(None, description="Industry")
    image_url: str | None = Field(None, description="Primary profile image URL")
    tags: list[str] = Field(default_factory=list, description="Tags")
    primary_contact: PrimaryContactInfo = Field(..., description="Primary contact information")
    company_contacts: list[CompanyContact] = Field(
        default_factory=list,
        description="All contacts linked to this company (company type only)",
    )
    websites: list[Website] = Field(default_factory=list, description="Websites")
    billing_preferences: BillingPreferences | None = Field(None, description="Billing preferences")
    custom_fields: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Resolved FieldCells: field_id, instance_id, type, field_key, label, "
            "and value | sub_fields | items"
        ),
    )
    addresses: list[ClientAddressResponse] = Field(default_factory=list, description="Addresses")
    leads: list[LeadInfo] = Field(
        default_factory=list,
        description=(
            "Leads linked to this client (company and/or contact), newest first by "
            "``updated_at``. Use the first item as the primary pipeline snapshot when only "
            "one lead exists."
        ),
    )
    additional_data: dict[str, Any] = Field(
        default_factory=dict, description="Dynamic data stored as passed"
    )
    sales_intelligence: dict[str, Any] = Field(
        default_factory=dict,
        description="Sales intelligence insights (populated by enrichment when available)",
    )
    social_pages: list[SocialPage] = Field(
        default_factory=list, description="Social platform and URL entries"
    )
    # Individual (person) type fields
    work_history: list[WorkHistoryItem] = Field(
        default_factory=list, description="Work history (person type)"
    )
    educational_history: list[EducationalHistoryItem] = Field(
        default_factory=list, description="Educational history (person type)"
    )
    skills: list[str] = Field(default_factory=list, description="Skills (person type)")
    # Company type fields
    target_market_segments: list[str] = Field(
        default_factory=list, description="Target market segments (company type)"
    )
    current_tech_stack: list[str] = Field(
        default_factory=list, description="Current tech stack (company type)"
    )
    description: str | None = Field(None, description="Description (company type)")
    preferred_communication_channels: list[str] = Field(
        default_factory=list, description="Preferred communication channels (company type)"
    )
    industry_specific_terminologies: list[str] = Field(
        default_factory=list, description="Industry-specific terminologies (company type)"
    )
    linked_pages: list[LinkedPageItem] = Field(
        default_factory=list, description="Linked pages (company type)"
    )
    products: list[Product] = Field(default_factory=list, description="Products (company type)")
    key_people: list[KeyPerson] = Field(
        default_factory=list, description="Key people (company type)"
    )
    enrichment_done: bool = Field(default=False, description="Whether enrichment has been run")
    last_enriched_at: str | None = Field(None, description="Last enrichment timestamp")
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Update timestamp")
