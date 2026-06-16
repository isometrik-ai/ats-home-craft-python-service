"""Schemas for `/integrations/clients/*` endpoints.

These endpoints reuse the same split-table DTOs as `/companies/*` and `/contacts/*`.

This module only keeps external-specific response shapes that are not part of the
resource APIs (e.g., returning created identifiers).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums import EntityType, FieldType


class ExternalVariableEntityType(str, Enum):
    """Entity types supported by GET /integrations/clients/variables."""

    CONTACT = EntityType.CONTACT.value
    COMPANY = EntityType.COMPANY.value
    LEAD = EntityType.LEAD.value


EXTERNAL_VARIABLE_ENTITY_TO_ENTITY_TYPE: dict[ExternalVariableEntityType, EntityType] = {
    ExternalVariableEntityType.CONTACT: EntityType.CONTACT,
    ExternalVariableEntityType.COMPANY: EntityType.COMPANY,
    ExternalVariableEntityType.LEAD: EntityType.LEAD,
}


class ExternalCreateCompanyResult(BaseModel):
    """External create response for company create."""

    model_config = ConfigDict(extra="forbid")

    company_id: str
    contact_id: str | None = None
    lead_id: str | None = None


class ExternalCreateContactResult(BaseModel):
    """External create response for contact create."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str
    company_id: str | None = None
    lead_id: str | None = None


class ExternalContactFieldsByPhoneRequest(BaseModel):
    """Request to fetch selected contact fields by phone number."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    phone_number: str = Field(
        ...,
        min_length=5,
        max_length=64,
        validation_alias=AliasChoices("phone_number", "phoneNumber"),
    )
    variable_keys: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("variable_keys", "variableKeys"),
        description=(
            "Optional variable keys to resolve. When omitted or empty, returns all scalar "
            "variables from GET /integrations/clients/variables?entity_type=contact."
        ),
    )


class ExternalContactFieldValue(BaseModel):
    """A single requested field key/value pair."""

    model_config = ConfigDict(extra="forbid")

    variable_key: str
    variable_value: str = ""


class ExternalEntityVariableDefinition(BaseModel):
    """Catalog entry for an entity variable (fixed column or custom field definition)."""

    model_config = ConfigDict(extra="forbid")

    variable_key: str = Field(..., description="Key used in integrations and variable lookups")
    field_name: str = Field(..., description="Human-readable label")
    field_type: str = Field(..., description="Field type (text, number, date, address, etc.)")
    source: Literal["fixed", "custom"] = Field(
        ...,
        description="Whether the variable maps to a built-in entity column or a custom field",
    )
    description: str | None = None
    is_required: bool = False
    field_id: str | None = Field(
        default=None,
        description="Custom field definition id (present only when source=custom)",
    )


ExternalContactVariableDefinition = ExternalEntityVariableDefinition


CONTACT_FIXED_VARIABLE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "variable_key": "name",
        "field_name": "Full Name",
        "field_type": FieldType.TEXT.value,
        "description": "Computed from prefix, first, middle, and last name.",
    },
    {
        "variable_key": "prefix",
        "field_name": "Prefix",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "first_name",
        "field_name": "First Name",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "middle_name",
        "field_name": "Middle Name",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "last_name",
        "field_name": "Last Name",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "title",
        "field_name": "Title",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "email",
        "field_name": "Email",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "phone_number",
        "field_name": "Phone Number",
        "field_type": FieldType.TEXT.value,
        "description": "Primary phone number from the contact phones list.",
    },
    {
        "variable_key": "phone_isd_code",
        "field_name": "Phone ISD Code",
        "field_type": FieldType.TEXT.value,
        "description": "ISD/country code for the primary phone number.",
    },
    {
        "variable_key": "date_of_birth",
        "field_name": "Date of Birth",
        "field_type": FieldType.DATE.value,
    },
    {
        "variable_key": "profile_photo_url",
        "field_name": "Profile Photo URL",
        "field_type": FieldType.URL.value,
    },
    {
        "variable_key": "external_contact_id",
        "field_name": "External Contact ID",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "status",
        "field_name": "Status",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "description",
        "field_name": "Description",
        "field_type": FieldType.LONG_TEXT.value,
    },
    {
        "variable_key": "tags",
        "field_name": "Tags",
        "field_type": FieldType.TEXT.value,
        "description": "Comma-separated contact tags.",
    },
    {
        "variable_key": "skills",
        "field_name": "Skills",
        "field_type": FieldType.TEXT.value,
        "description": "Comma-separated contact skills.",
    },
    {
        "variable_key": "address",
        "field_name": "Address",
        "field_type": FieldType.ADDRESS.value,
        "description": "Primary contact address object.",
    },
)


COMPANY_FIXED_VARIABLE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "variable_key": "name",
        "field_name": "Company Name",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "industry",
        "field_name": "Industry",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "profile_photo_url",
        "field_name": "Profile Photo URL",
        "field_type": FieldType.URL.value,
    },
    {
        "variable_key": "email",
        "field_name": "Email",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "phone_number",
        "field_name": "Phone Number",
        "field_type": FieldType.TEXT.value,
        "description": "Primary phone number from the company phones list.",
    },
    {
        "variable_key": "phone_isd_code",
        "field_name": "Phone ISD Code",
        "field_type": FieldType.TEXT.value,
        "description": "ISD/country code for the primary phone number.",
    },
    {
        "variable_key": "status",
        "field_name": "Status",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "description",
        "field_name": "Description",
        "field_type": FieldType.LONG_TEXT.value,
    },
    {
        "variable_key": "address",
        "field_name": "Address",
        "field_type": FieldType.ADDRESS.value,
        "description": "Primary company address object.",
    },
)


LEAD_FIXED_VARIABLE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "variable_key": "name",
        "field_name": "Lead Name",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "stage_id",
        "field_name": "Stage ID",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "stage_name",
        "field_name": "Stage Name",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "deal_type",
        "field_name": "Deal Type",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "priority",
        "field_name": "Priority",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "lead_source",
        "field_name": "Lead Source",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "referral_source",
        "field_name": "Referral Source",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "lead_score",
        "field_name": "Lead Score",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "close_date",
        "field_name": "Close Date",
        "field_type": FieldType.DATE.value,
    },
    {
        "variable_key": "amount",
        "field_name": "Amount",
        "field_type": FieldType.NUMBER.value,
    },
    {
        "variable_key": "currency",
        "field_name": "Currency",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "description",
        "field_name": "Description",
        "field_type": FieldType.LONG_TEXT.value,
    },
    {
        "variable_key": "owner_id",
        "field_name": "Owner ID",
        "field_type": FieldType.TEXT.value,
    },
    {
        "variable_key": "owner_name",
        "field_name": "Owner Name",
        "field_type": FieldType.TEXT.value,
    },
)


ENTITY_FIXED_VARIABLE_DEFINITIONS: dict[EntityType, tuple[dict[str, Any], ...]] = {
    EntityType.CONTACT: CONTACT_FIXED_VARIABLE_DEFINITIONS,
    EntityType.COMPANY: COMPANY_FIXED_VARIABLE_DEFINITIONS,
    EntityType.LEAD: LEAD_FIXED_VARIABLE_DEFINITIONS,
}


# Field types resolvable as a single scalar value via external variable lookup.
SCALAR_ENTITY_VARIABLE_FIELD_TYPES: frozenset[str] = frozenset(
    {
        FieldType.TEXT.value,
        FieldType.NUMBER.value,
        FieldType.DATE.value,
        FieldType.YES_NO.value,
        FieldType.URL.value,
        FieldType.LONG_TEXT.value,
        FieldType.RICH_TEXT.value,
        FieldType.DROPDOWN.value,
        FieldType.RANGE_SLIDER.value,
        FieldType.CURRENCY.value,
        FieldType.ADDRESS.value,
    }
)


SCALAR_CONTACT_VARIABLE_FIELD_TYPES = SCALAR_ENTITY_VARIABLE_FIELD_TYPES


__all__ = [
    "COMPANY_FIXED_VARIABLE_DEFINITIONS",
    "CONTACT_FIXED_VARIABLE_DEFINITIONS",
    "ENTITY_FIXED_VARIABLE_DEFINITIONS",
    "EXTERNAL_VARIABLE_ENTITY_TO_ENTITY_TYPE",
    "ExternalVariableEntityType",
    "LEAD_FIXED_VARIABLE_DEFINITIONS",
    "SCALAR_CONTACT_VARIABLE_FIELD_TYPES",
    "SCALAR_ENTITY_VARIABLE_FIELD_TYPES",
    "ExternalCreateCompanyResult",
    "ExternalCreateContactResult",
    "ExternalContactFieldsByPhoneRequest",
    "ExternalContactFieldValue",
    "ExternalContactVariableDefinition",
    "ExternalEntityVariableDefinition",
]
