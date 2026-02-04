"""Client Management Schemas Module.

This module contains Pydantic models for client management operations.
"""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator

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

    url: str = Field(..., description="Website URL", max_length=500)
    type: str = Field(..., description="Website type", max_length=50)
    is_primary: bool = Field(default=False, description="Primary website flag")


class Address(BaseModel):
    """Address schema."""

    address_line1: str = Field(..., description="Primary address line", max_length=200)
    address_line2: str | None = Field(None, description="Secondary address line", max_length=200)
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


class CreateClientRequest(BaseModel):
    """Request schema for creating a client."""

    client_type: ClientType = Field(..., description="Client type")
    email: str = Field(..., description="Email address")
    phone_isd_code: str = Field(..., description="Phone ISD code")
    phone_number: str = Field(..., description="Phone number")

    # Name fields (required for both types)
    first_name: str = Field(..., description="First name", max_length=100)
    last_name: str = Field(..., description="Last name", max_length=100)

    # Person type fields
    prefix: str | None = Field(None, description="Name prefix", max_length=10)
    middle_name: str | None = Field(None, description="Middle name", max_length=100)
    company: str | None = Field(None, description="Company name", max_length=200)
    title: str | None = Field(None, description="Job title", max_length=100)
    date_of_birth: date | None = Field(None, description="Date of birth")

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
    custom_fields: dict[str, str] = Field(default_factory=dict, description="Custom fields")
    portal_access: bool = Field(default=False, description="Portal access enabled flag")

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


class CreateClientFromUserRequest(BaseModel):
    """Request schema for creating a client from user ID."""

    user_id: str = Field(..., description="User ID from auth.users table")
    organization_id: str = Field(..., description="Organization ID")


class PrimaryContactInfo(BaseModel):
    """Primary contact information schema."""

    first_name: str | None = Field(None, description="First name")
    last_name: str | None = Field(None, description="Last name")
    title: str | None = Field(None, description="Job title")
    email: str | None = Field(None, description="Email address")
    phone_isd_code: str | None = Field(None, description="Phone ISD code")
    phone: str | None = Field(None, description="Phone number")


class ClientListResponse(BaseModel):
    """Client list response schema."""

    id: str = Field(..., description="Client ID")
    name: str = Field(..., description="Client name")
    primary_contact: PrimaryContactInfo = Field(..., description="Primary contact information")
    company_type: ClientType = Field(..., description="Client type")
    status: ClientStatus = Field(..., description="Client status")
    matters: list[Any] = Field(default_factory=list, description="Matters list")
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


class ClientDetailsResponse(BaseModel):
    """Client details response schema."""

    id: str = Field(..., description="Client ID")
    organization_id: str = Field(..., description="Organization ID")
    client_type: ClientType = Field(..., description="Client type")
    name: str = Field(..., description="Client name")
    status: ClientStatus = Field(..., description="Client status")
    industry: str | None = Field(None, description="Industry")
    profile_photo_url: str | None = Field(None, description="Profile photo URL")
    tags: list[str] = Field(default_factory=list, description="Tags")
    primary_contact: PrimaryContactInfo = Field(..., description="Primary contact information")
    websites: list[Website] = Field(default_factory=list, description="Websites")
    billing_preferences: BillingPreferences | None = Field(None, description="Billing preferences")
    custom_fields: dict[str, str] = Field(default_factory=dict, description="Custom fields")
    addresses: list[ClientAddressResponse] = Field(default_factory=list, description="Addresses")
    lead: LeadInfo | None = Field(None, description="Lead information")
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Update timestamp")
