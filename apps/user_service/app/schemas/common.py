"""Common Schemas Module.

Shared schemas used across multiple modules to avoid circular dependencies.

This module contains:
- org/auth related DTOs (existing)
- shared client/contact/company DTOs copied from legacy `clients` schemas
  so resource-specific modules can depend on *common* without tight coupling.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.user_service.app.schemas.enums import AddressType, PlanType
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

NonEmptyStr = Annotated[str, Field(min_length=1)]


class OrganizationAddress(BaseModel):
    """Address information used in org/auth DTOs."""

    address_line: str | None = Field(None, description="Address line")
    city: str | None = Field(None, description="City")
    state: str | None = Field(None, description="State")
    zip_code: str | None = Field(None, description="Zip code")
    country: str = Field(..., description="Country name")


class Subscription(BaseModel):
    """Subscription information."""

    max_users: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of licensed seats for the organization",
    )
    users: int = Field(
        default=1,
        ge=0,
        description="Current number of organization members (licensed seats in use)",
    )
    plan_type: PlanType = Field(
        default=PlanType.TRIAL,
        description="Current subscription plan type",
    )
    start_date: str | None = Field(
        default=None,
        description="ISO timestamp when the subscription becomes active",
    )
    end_date: str | None = Field(
        default=None,
        description="ISO timestamp when the subscription expires",
    )


class OrganizationBasicDetails(BaseModel):
    """Model for organization basic details"""

    id: str = Field(..., description="Unique identifier for the organization")
    name: str = Field(..., description="Organization's name")
    domain: str | None = Field(None, description="Organization's domain name")
    logo_url: str | None = Field(None, description="URL to organization's logo")
    description: str | None = Field(None, description="Organization's description")
    company_size: str | None = Field(None, description="Organization's company size")
    address: OrganizationAddress | None = Field(None, description="Organization's address")
    subscription: Subscription | None = Field(None, description="Organization's subscription")
    primary_practice_areas: list[NonEmptyStr] | None = Field(
        None, description="Organization's primary practice areas"
    )
    secondary_practice_areas: list[NonEmptyStr] | None = Field(
        None, description="Organization's secondary practice areas"
    )


class NoteItem(BaseModel):
    """One structured note item stored as a JSONB array element."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., max_length=500)
    content: str = Field(..., max_length=50000)

    @field_validator("title", "content", mode="before")
    @classmethod
    def strip_whitespace(cls, value: str) -> str:
        """Strip whitespace from the title and content."""
        return value.strip()

    @field_validator("title", "content")
    @classmethod
    def non_empty_after_strip(cls, value: str) -> str:
        """Raise ValueError if the value is empty after stripping whitespace."""
        if not value:
            raise ValueError("must not be empty")
        return value


# =====================================================================
# Shared client/contact/company models (mirrors legacy clients schema)
# =====================================================================


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
    """Address schema (legacy client/contact/company address shape)."""

    address_line1: str | None = Field(None, description="Primary address line", max_length=1000)
    address_line2: str | None = Field(None, description="Secondary address line", max_length=1000)
    city: str | None = Field(None, description="City", max_length=100)
    state: str | None = Field(None, description="State/Province", max_length=100)
    postal_code: str | None = Field(None, description="Postal/ZIP code", max_length=20)
    country: str = Field(default="United States", description="Country", max_length=100)
    address_type: AddressType | None = Field(None, description="Address type")
    is_primary: bool = Field(default=False, description="Primary address flag")


class BillingPreferences(BaseModel):
    """Billing preferences schema."""

    method: str | None = Field(None, description="Billing method", max_length=50)
    terms: str | None = Field(None, description="Payment terms", max_length=50)


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


# Input schemas for add operations (no id field)
class AddressInput(BaseModel):
    """Address input for add operation."""

    place_id: str | None = Field(None, max_length=200)
    address_line1: str | None = Field(None, max_length=1000, description="Primary address line")
    address_line2: str | None = Field(None, max_length=1000)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    country: str | None = Field(None, max_length=100)
    latitude: float | None = None
    longitude: float | None = None
    address_type: AddressType | None = None
    address_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra address metadata (e.g. formatted line). Defaults to {} when omitted.",
    )
    is_primary: bool = Field(default=False, description="Primary address flag")

    @field_validator("address_data", mode="before")
    @classmethod
    def _coerce_address_data(cls, value: Any) -> dict[str, Any]:
        """Use an empty object when address_data is omitted or explicitly null."""
        return {} if value is None else value


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


class WorkHistoryItem(BaseModel):
    """Work history item (person type)."""

    id: str | None = Field(None, description="Work history item ID")
    job_title: str | None = Field(None, description="Job title", max_length=200)
    company: str | None = Field(None, description="Company name", max_length=200)
    start_date: str | None = Field(None, description="Start date (e.g. Jan 2023)", max_length=50)
    end_date: str | None = Field(None, description="End date (e.g. Feb 2023)", max_length=50)
    current: bool | None = Field(None, description="Currently employed here")


class WorkHistoryInput(BaseModel):
    """Work history input for add operation."""

    job_title: str | None = Field(None, max_length=200)
    company: str | None = Field(None, max_length=200)
    start_date: str | None = Field(None, max_length=50, description="e.g. Jan 2023")
    end_date: str | None = Field(None, max_length=50, description="e.g. Feb 2023")
    current: bool | None = None


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
    university: str | None = Field(None, description="University name", max_length=300)
    degree: str | None = Field(None, description="Degree", max_length=200)
    field_of_study: str | None = Field(None, description="Field of study", max_length=200)
    start_date: str | None = Field(None, description="Start date (e.g. Sep 2018)", max_length=50)
    end_date: str | None = Field(None, description="End date (e.g. May 2022)", max_length=50)


class EducationalHistoryInput(BaseModel):
    """Educational history input for add operation."""

    university: str | None = Field(None, max_length=300)
    degree: str | None = Field(None, max_length=200)
    field_of_study: str | None = Field(None, max_length=200)
    start_date: str | None = Field(None, max_length=50)
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


class AddressesUpdate(BaseModel):
    """Batch address operations: add, update, and/or remove."""

    add: list[AddressInput] | None = Field(None, description="New addresses to add", max_length=50)
    update: list[AddressUpdateItem] | None = Field(
        None, description="Existing addresses to update (must include id)", max_length=50
    )
    remove: list[str] | None = Field(
        None, description="Address record IDs to remove", max_length=50
    )

    @model_validator(mode="after")
    def validate_primary_address_across_add_and_update(self) -> "AddressesUpdate":
        """Validate only one primary address across add/update payload."""
        primary_add_count = sum(1 for a in (self.add or []) if a.is_primary is True)
        primary_update_count = sum(1 for a in (self.update or []) if a.is_primary is True)
        if primary_add_count + primary_update_count > 1:
            raise ValidationException(
                message_key="clients.errors.only_one_primary_address",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


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


class BillingPreferencesUpdate(BaseModel):
    """Partial billing preferences; merged with existing."""

    method: str | None = Field(None, max_length=50)
    terms: str | None = Field(None, max_length=50)
