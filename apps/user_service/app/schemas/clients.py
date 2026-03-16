"""Client Management Schemas Module.

This module contains Pydantic models for client management operations.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.enums import (
    AddressType,
    ClientStatus,
    ClientType,
    IntakeStage,
    LeadStatus,
)
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class Website(BaseModel):
    """Website schema."""

    id: str | None = Field(None, description="Website ID")
    url: str = Field(..., description="Website URL", max_length=500)
    type: str = Field(..., description="Website type", max_length=50)
    is_primary: bool = Field(default=False, description="Primary website flag")


class SocialPage(BaseModel):
    """Social media page schema: platform (e.g. linkedin, instagram, twitter) and URL."""

    id: str | None = Field(None, description="Social page ID")
    platform: str = Field(..., description="Platform name", max_length=50)
    url: str = Field(..., description="Profile/page URL", max_length=500)


class Address(BaseModel):
    """Address schema."""

    address_line1: str = Field(..., description="Primary address line", max_length=1000)
    address_line2: str | None = Field(None, description="Secondary address line", max_length=1000)
    city: str | None = Field(None, description="City", max_length=100)
    state: str | None = Field(None, description="State/Province", max_length=100)
    postal_code: str | None = Field(None, description="Postal/ZIP code", max_length=20)
    country: str = Field(default="United States", description="Country", max_length=100)
    address_type: AddressType | None = Field(None, description="Address type")
    is_primary: bool = Field(default=False, description="Primary address flag")


class LeadManagement(BaseModel):
    """Lead management schema."""

    enabled: bool = Field(default=False, description="Enable lead management")
    lead_status: LeadStatus | None = Field(None, description="Lead status")
    intake_stage: IntakeStage | None = Field(None, description="Intake stage")
    lead_source: str | None = Field(None, description="Lead source", max_length=100)
    referral_source: str | None = Field(None, description="Referral source", max_length=200)
    lead_score: str | None = Field(None, description="Lead score")


class BillingPreferences(BaseModel):
    """Billing preferences schema."""

    method: str | None = Field(None, description="Billing method", max_length=50)
    terms: str | None = Field(None, description="Payment terms", max_length=50)


# Input schemas for add operations (no id field)
class AddressInput(BaseModel):
    """Address input for add operation."""

    place_id: str | None = Field(None, max_length=200)
    address_line1: str = Field(..., max_length=1000, description="Primary address line")
    address_line2: str | None = Field(None, max_length=1000)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    country: str | None = Field(None, max_length=100)
    latitude: float | None = None
    longitude: float | None = None
    address_type: AddressType | None = None
    address_data: dict[str, Any] | None = None
    is_primary: bool = Field(default=False, description="Primary address flag")


class WebsiteInput(BaseModel):
    """Website input for add operation."""

    url: str = Field(..., max_length=500, description="Website URL")
    type: str = Field(..., max_length=50, description="Website type")
    is_primary: bool = Field(default=False, description="Primary website flag")


class SocialPageInput(BaseModel):
    """Social page input for add operation."""

    platform: str = Field(..., max_length=50, description="Platform name")
    url: str = Field(..., max_length=500, description="Profile/page URL")


# Update item schemas (requires id)
class AddressUpdateItem(BaseModel):
    """Address update item; only provided fields are updated."""

    id: str = Field(..., description="Address record ID to update")
    place_id: str | None = Field(None, max_length=200)
    address_line1: str | None = Field(None, max_length=1000)
    address_line2: str | None = Field(None, max_length=1000)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    country: str | None = Field(None, max_length=100)
    latitude: float | None = None
    longitude: float | None = None
    address_type: AddressType | None = None
    address_data: dict[str, Any] | None = None
    is_primary: bool | None = None


class WebsiteUpdateItem(BaseModel):
    """Website update item; only provided fields are updated."""

    id: str = Field(..., description="Website ID to update")
    url: str | None = Field(None, max_length=500)
    type: str | None = Field(None, max_length=50)
    is_primary: bool | None = None


class SocialPageUpdateItem(BaseModel):
    """Social page update item; only provided fields are updated."""

    id: str = Field(..., description="Social page ID to update")
    platform: str | None = Field(None, max_length=50)
    url: str | None = Field(None, max_length=500)


# --- Primary contact phones (stored on client_users) ---
class Phone(BaseModel):
    """Phone number item: id, phone_number, phone_isd_code, label, is_primary."""

    id: str | None = Field(None, description="Phone item ID")
    phone_number: str = Field(..., description="Phone number", max_length=50)
    phone_isd_code: str = Field(..., description="Phone ISD code", max_length=10)
    label: str | None = Field(None, description="Label (e.g. mobile, work)", max_length=50)
    is_primary: bool = Field(default=False, description="Primary phone flag")


class PhoneInput(BaseModel):
    """Phone input for add operation (no id)."""

    phone_number: str = Field(..., max_length=50, description="Phone number")
    phone_isd_code: str = Field(..., max_length=10, description="Phone ISD code")
    label: str | None = Field(None, max_length=50, description="Label (e.g. mobile, work)")
    is_primary: bool = Field(default=False, description="Primary phone flag")


class PhoneUpdateItem(BaseModel):
    """Phone update item; only provided fields are updated."""

    id: str = Field(..., description="Phone item ID to update")
    phone_number: str | None = Field(None, max_length=50)
    phone_isd_code: str | None = Field(None, max_length=10)
    label: str | None = Field(None, max_length=50)
    is_primary: bool | None = None


class PhonesUpdate(BaseModel):
    """Batch phone operations: add, update, and/or remove (primary contact)."""

    add: list[PhoneInput] | None = Field(None, max_length=20)
    update: list[PhoneUpdateItem] | None = Field(None, max_length=20)
    remove: list[str] | None = Field(None, max_length=20)

    @field_validator("add")
    @classmethod
    def validate_primary_phone_add(
        cls, add_list: list[PhoneInput] | None
    ) -> list[PhoneInput] | None:
        """Validate only one primary phone on add."""
        if not add_list:
            return add_list
        if sum(1 for p in add_list if p.is_primary) > 1:
            raise ValidationException(
                message_key="clients.errors.only_one_primary_phone",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return add_list

    @field_validator("update")
    @classmethod
    def validate_primary_phone_update(
        cls, update_list: list[PhoneUpdateItem] | None
    ) -> list[PhoneUpdateItem] | None:
        """Validate only one primary phone on update."""
        if not update_list:
            return update_list
        if sum(1 for p in update_list if p.is_primary is True) > 1:
            raise ValidationException(
                message_key="clients.errors.only_one_primary_phone",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return update_list


# --- Individual (person) type: work_history, educational_history ---
class WorkHistoryItem(BaseModel):
    """Work history item (person type)."""

    id: str | None = Field(None, description="Work history item ID")
    job_title: str = Field(..., description="Job title", max_length=200)
    company: str = Field(..., description="Company name", max_length=200)
    start_date: str = Field(..., description="Start date (e.g. Jan 2023)", max_length=50)
    end_date: str | None = Field(None, description="End date (e.g. Feb 2023)", max_length=50)
    current: bool = Field(default=False, description="Currently employed here")


class WorkHistoryInput(BaseModel):
    """Work history input for add operation."""

    job_title: str = Field(..., max_length=200)
    company: str = Field(..., max_length=200)
    start_date: str = Field(..., max_length=50, description="e.g. Jan 2023")
    end_date: str | None = Field(None, max_length=50, description="e.g. Feb 2023")
    current: bool = False


class WorkHistoryUpdateItem(BaseModel):
    """Work history update item; only provided fields are updated."""

    id: str = Field(..., description="Work history item ID to update")
    job_title: str | None = Field(None, max_length=200)
    company: str | None = Field(None, max_length=200)
    start_date: str | None = Field(None, max_length=50)
    end_date: str | None = Field(None, max_length=50)
    current: bool | None = None


class WorkHistoryUpdate(BaseModel):
    """Batch work history operations: add, update, and/or remove (person type)."""

    add: list[WorkHistoryInput] | None = Field(None, max_length=50)
    update: list[WorkHistoryUpdateItem] | None = Field(None, max_length=50)
    remove: list[str] | None = Field(None, max_length=50)


class EducationalHistoryItem(BaseModel):
    """Educational history item (person type)."""

    id: str | None = Field(None, description="Educational history item ID")
    university: str = Field(..., description="University name", max_length=300)
    degree: str = Field(..., description="Degree", max_length=200)
    field_of_study: str = Field(..., description="Field of study", max_length=200)
    start_date: str = Field(..., description="Start date (e.g. Sep 2018)", max_length=50)
    end_date: str | None = Field(None, description="End date (e.g. May 2022)", max_length=50)


class EducationalHistoryInput(BaseModel):
    """Educational history input for add operation."""

    university: str = Field(..., max_length=300)
    degree: str = Field(..., max_length=200)
    field_of_study: str = Field(..., max_length=200)
    start_date: str = Field(..., max_length=50)
    end_date: str | None = Field(None, max_length=50)


class EducationalHistoryUpdateItem(BaseModel):
    """Educational history update item; only provided fields are updated."""

    id: str = Field(..., description="Educational history item ID to update")
    university: str | None = Field(None, max_length=300)
    degree: str | None = Field(None, max_length=200)
    field_of_study: str | None = Field(None, max_length=200)
    start_date: str | None = Field(None, max_length=50)
    end_date: str | None = Field(None, max_length=50)


class EducationalHistoryUpdate(BaseModel):
    """Batch educational history operations: add, update, and/or remove (person type)."""

    add: list[EducationalHistoryInput] | None = Field(None, max_length=50)
    update: list[EducationalHistoryUpdateItem] | None = Field(None, max_length=50)
    remove: list[str] | None = Field(None, max_length=50)


# --- Company type: linked_pages ---
class LinkedPageItem(BaseModel):
    """Linked page item (company type)."""

    id: str | None = Field(None, description="Linked page ID")
    page_name: str = Field(..., description="Page name", max_length=200)
    page_url: str = Field(..., description="Page URL", max_length=500)


class LinkedPageInput(BaseModel):
    """Linked page input for add operation."""

    page_name: str = Field(..., max_length=200)
    page_url: str = Field(..., max_length=500)


class LinkedPageUpdateItem(BaseModel):
    """Linked page update item; only provided fields are updated."""

    id: str = Field(..., description="Linked page ID to update")
    page_name: str | None = Field(None, max_length=200)
    page_url: str | None = Field(None, max_length=500)


class LinkedPagesUpdate(BaseModel):
    """Batch linked page operations: add, update, and/or remove (company type)."""

    add: list[LinkedPageInput] | None = Field(None, max_length=50)
    update: list[LinkedPageUpdateItem] | None = Field(None, max_length=50)
    remove: list[str] | None = Field(None, max_length=50)


# --- Company type: products, key_people ---
class Product(BaseModel):
    """Product item (company type)."""

    id: str | None = Field(None, description="Product item ID")
    name: str = Field(..., description="Product name", max_length=200)
    url: str | None = Field(None, description="Product URL", max_length=500)
    description: str | None = Field(None, description="Product description", max_length=2000)


class ProductInput(BaseModel):
    """Product input for add operation."""

    name: str = Field(..., max_length=200, description="Product name")
    url: str | None = Field(None, max_length=500, description="Product URL")
    description: str | None = Field(None, max_length=2000, description="Product description")


class ProductUpdateItem(BaseModel):
    """Product update item; only provided fields are updated."""

    id: str = Field(..., description="Product item ID to update")
    name: str | None = Field(None, max_length=200)
    url: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=2000)


class ProductsUpdate(BaseModel):
    """Batch product operations: add, update, and/or remove (company type)."""

    add: list[ProductInput] | None = Field(None, max_length=50)
    update: list[ProductUpdateItem] | None = Field(None, max_length=50)
    remove: list[str] | None = Field(None, max_length=50)


class KeyPerson(BaseModel):
    """Key person item (company type)."""

    id: str | None = Field(None, description="Key person item ID")
    name: str = Field(..., description="Person name", max_length=200)
    title: str | None = Field(None, description="Job title", max_length=200)
    linkedin: str | None = Field(None, description="LinkedIn profile URL", max_length=500)


class KeyPersonInput(BaseModel):
    """Key person input for add operation."""

    name: str = Field(..., max_length=200, description="Person name")
    title: str | None = Field(None, max_length=200, description="Job title")
    linkedin: str | None = Field(None, max_length=500, description="LinkedIn profile URL")


class KeyPersonUpdateItem(BaseModel):
    """Key person update item; only provided fields are updated."""

    id: str = Field(..., description="Key person item ID to update")
    name: str | None = Field(None, max_length=200)
    title: str | None = Field(None, max_length=200)
    linkedin: str | None = Field(None, max_length=500)


class KeyPeopleUpdate(BaseModel):
    """Batch key people operations: add, update, and/or remove (company type)."""

    add: list[KeyPersonInput] | None = Field(None, max_length=50)
    update: list[KeyPersonUpdateItem] | None = Field(None, max_length=50)
    remove: list[str] | None = Field(None, max_length=50)


# Delta update wrappers (add/update/remove) - supports batch operations
class AddressesUpdate(BaseModel):
    """Batch address operations: add, update, and/or remove."""

    add: list[AddressInput] | None = Field(None, description="New addresses to add", max_length=50)
    update: list[AddressUpdateItem] | None = Field(
        None, description="Existing addresses to update (must include id)", max_length=50
    )
    remove: list[str] | None = Field(
        None, description="Address record IDs to remove", max_length=50
    )

    @field_validator("add")
    @classmethod
    def validate_primary_address_add(
        cls, add_list: list[AddressInput] | None
    ) -> list[AddressInput] | None:
        """Validate only one primary address in add operations."""
        if not add_list:
            return add_list
        primary_count = sum(1 for a in add_list if a.is_primary is True)
        if primary_count > 1:
            raise ValidationException(
                message_key="clients.errors.only_one_primary_address",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return add_list

    @field_validator("update")
    @classmethod
    def validate_primary_address_update(
        cls, update_list: list[AddressUpdateItem] | None
    ) -> list[AddressUpdateItem] | None:
        """Validate only one primary address in update operations."""
        if not update_list:
            return update_list
        primary_count = sum(1 for a in update_list if a.is_primary is True)
        if primary_count > 1:
            raise ValidationException(
                message_key="clients.errors.only_one_primary_address",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return update_list


class WebsitesUpdate(BaseModel):
    """Batch website operations: add, update, and/or remove."""

    add: list[WebsiteInput] | None = Field(None, description="New websites to add", max_length=50)
    update: list[WebsiteUpdateItem] | None = Field(
        None, description="Existing websites to update (must include id)", max_length=50
    )
    remove: list[str] | None = Field(None, description="Website IDs to remove", max_length=50)

    @field_validator("add")
    @classmethod
    def validate_primary_website_add(
        cls, add_list: list[WebsiteInput] | None
    ) -> list[WebsiteInput] | None:
        """Validate only one primary website in add operations."""
        if not add_list:
            return add_list
        primary_count = sum(1 for w in add_list if w.is_primary is True)
        if primary_count > 1:
            raise ValidationException(
                message_key="clients.errors.only_one_primary_website",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return add_list

    @field_validator("update")
    @classmethod
    def validate_primary_website_update(
        cls, update_list: list[WebsiteUpdateItem] | None
    ) -> list[WebsiteUpdateItem] | None:
        """Validate only one primary website in update operations."""
        if not update_list:
            return update_list
        primary_count = sum(1 for w in update_list if w.is_primary is True)
        if primary_count > 1:
            raise ValidationException(
                message_key="clients.errors.only_one_primary_website",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return update_list


class SocialPagesUpdate(BaseModel):
    """Batch social page operations: add, update, and/or remove."""

    add: list[SocialPageInput] | None = Field(
        None, description="New social pages to add", max_length=50
    )
    update: list[SocialPageUpdateItem] | None = Field(
        None, description="Existing social pages to update (must include id)", max_length=50
    )
    remove: list[str] | None = Field(None, description="Social page IDs to remove", max_length=50)


class LeadManagementUpdate(BaseModel):
    """Lead update by lead_id."""

    lead_id: str = Field(..., description="Lead ID to update")
    enabled: bool | None = None
    lead_status: LeadStatus | None = None
    intake_stage: IntakeStage | None = None
    lead_source: str | None = Field(None, max_length=100)
    referral_source: str | None = Field(None, max_length=200)
    lead_score: str | None = None
    notes: str | None = None


class BillingPreferencesUpdate(BaseModel):
    """Partial billing preferences; merged with existing."""

    method: str | None = Field(None, max_length=50)
    terms: str | None = Field(None, max_length=50)


class PrimaryContactUpdate(BaseModel):
    """Primary contact PATCH; only provided fields are applied."""

    phones: PhonesUpdate | None = Field(
        None, description="Batch phone operations: add, update, and/or remove"
    )


class UpdateClientRequest(BaseModel):
    """Client PATCH payload; only provided fields are applied.

    List-type fields (websites, addresses, social_pages) support batch operations:
    multiple add, update, and/or remove operations per field.
    """

    model_config = ConfigDict(extra="forbid")

    client_name: str | None = Field(None, max_length=200)
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
    custom_fields: dict[str, Any] | None = Field(
        None,
        description=(
            "Custom fields (validated against custom field definitions for the client type)"
        ),
    )
    additional_data: dict[str, Any] | None = None
    enrichment_done: bool | None = None
    last_enriched_at: datetime | None = None


class CreateClientRequest(BaseModel):
    """Request schema for creating a client."""

    client_type: ClientType = Field(..., description="Client type")
    email: str = Field(..., description="Email address")
    phones: list[PhoneInput] = Field(
        default_factory=list,
        max_length=20,
        description="Phone numbers for primary contact",
    )

    # Name fields (required for both types)
    first_name: str = Field(..., description="First name", max_length=100)
    last_name: str = Field(..., description="Last name", max_length=100)

    # Person type fields
    prefix: str | None = Field(None, description="Name prefix", max_length=10)
    middle_name: str | None = Field(None, description="Middle name", max_length=100)
    company: str | None = Field(None, description="Company name", max_length=200)
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

    # Common fields
    profile_photo_url: str | None = Field(None, description="Profile photo URL", max_length=500)
    websites: list[Website] = Field(default_factory=list, description="Websites", max_length=10)
    addresses: list[Address] = Field(default_factory=list, description="Addresses", max_length=10)
    tags: list[str] = Field(default_factory=list, description="Tags", max_length=50)
    lead_management: LeadManagement | None = Field(None, description="Lead management")
    billing_preferences: BillingPreferences | None = Field(None, description="Billing preferences")
    custom_fields: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Custom fields (validated against custom field definitions for the client type)"
        ),
    )
    portal_access: bool = Field(default=False, description="Portal access enabled flag")
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
    """Lead information schema."""

    id: str = Field(..., description="Lead ID")
    lead_status: LeadStatus | None = Field(None, description="Lead status")
    intake_stage: IntakeStage | None = Field(None, description="Intake stage")
    lead_source: str | None = Field(None, description="Lead source")
    referral_source: str | None = Field(None, description="Referral source")
    lead_score: str | None = Field(None, description="Lead score")
    converted_at: str | None = Field(None, description="Conversion timestamp")
    notes: str | None = Field(None, description="Lead notes")
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Update timestamp")


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
    custom_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Custom fields (formatted according to custom field definitions)",
    )
    addresses: list[ClientAddressResponse] = Field(default_factory=list, description="Addresses")
    lead: LeadInfo | None = Field(None, description="Lead information")
    additional_data: dict[str, Any] = Field(
        default_factory=dict, description="Dynamic data stored as passed"
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
