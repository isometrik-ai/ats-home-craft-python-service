"""Schemas for `/integrations/clients/*` endpoints.

These endpoints reuse the same split-table DTOs as `/companies/*` and `/contacts/*`.

This module only keeps external-specific response shapes that are not part of the
resource APIs (e.g., returning created identifiers).
"""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


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
    variable_keys: list[str] = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("variable_keys", "variableKeys"),
    )


class ExternalContactFieldValue(BaseModel):
    """A single requested field key/value pair."""

    model_config = ConfigDict(extra="forbid")

    variable_key: str
    variable_value: object | None = None


__all__ = [
    "ExternalCreateCompanyResult",
    "ExternalCreateContactResult",
    "ExternalContactFieldsByPhoneRequest",
    "ExternalContactFieldValue",
]
