"""Pydantic models for Typesense documents.

This module defines the **payload contract** for documents we send to Typesense.
It is the last validation boundary before indexing.

Usage pattern (in services):
- Build a raw document dict from DB rows and computed fields
- Validate it with `TypesenseClientDocument.model_validate(raw)`
- Serialize with `model_dump(exclude_none=True)` and then filter to the actual
  Typesense schema fields (see `build_document_from_schema`)

Design rules:
- `extra="ignore"`: callers may pass a larger intermediate dict; unknown fields are
  dropped so we don't accidentally index schema-drift/typos.
- `strict=True`: we do **not** coerce types at indexing time. The DB/query layer must
  return correctly typed values (e.g. `lead_score` must already be `int | None`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TypesensePhoneEntry(BaseModel):
    """Stored phone item for Typesense contact docs."""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str | None = None
    phone_number: str | None = None
    phone_isd_code: str | None = None
    label: str | None = None
    is_primary: bool | None = None


class TypesenseClientDocument(BaseModel):
    """Document shape for the Typesense `clients` collection.

    This model is aligned with the Typesense collection schema in
    `apps.user_service.app.search.client_typesense_schema.CLIENT_COLLECTION_SCHEMA`.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str = Field(description="Client UUID (string) used as the Typesense document id.")
    organization_id: str = Field(description="Tenant/organization UUID (string).")
    client_type: str | None = Field(
        default=None,
        description="Client type (e.g. 'person' or 'company').",
    )
    status: str | None = Field(
        default=None,
        description="Client lifecycle status (e.g. 'active', 'inactive', 'prospect').",
    )
    name: str = Field(
        description="Display name indexed for search (company name or person display name)."
    )

    company_name: str | None = Field(
        default=None,
        description="Employer/company name for person clients (optional).",
    )
    primary_contact_first_name: str | None = Field(
        default=None,
        description="Primary contact first name (display-only in Typesense, optional).",
    )
    primary_contact_last_name: str | None = Field(
        default=None,
        description="Primary contact last name (display-only in Typesense, optional).",
    )
    primary_contact_full_name: str | None = Field(
        default=None,
        description="Primary contact full name used for searching person names (optional).",
    )
    primary_contact_title: str | None = Field(
        default=None,
        description="Primary contact job title (optional).",
    )
    email: str | None = Field(
        default=None,
        description="Primary contact email (optional).",
    )

    phone_numbers: list[str] | None = Field(
        default=None,
        description="Flattened list of phone numbers (typically E.164 strings, optional).",
    )
    tags: list[str] | None = Field(
        default=None,
        description="Client tags/labels used for filtering/facets (optional).",
    )
    industry: str | None = Field(
        default=None,
        description="Industry/category (optional).",
    )
    description: str | None = Field(
        default=None,
        description="Free-text company description (optional).",
    )
    target_market_segments: list[str] | None = Field(
        default=None,
        description="Target market segments (optional).",
    )
    current_tech_stack: list[str] | None = Field(
        default=None,
        description="Technology stack keywords (optional).",
    )
    industry_specific_terminologies: list[str] | None = Field(
        default=None,
        description="Domain jargon/terminology keywords (optional).",
    )
    preferred_communication_channels: list[str] | None = Field(
        default=None,
        description="Preferred communication channels (optional).",
    )
    key_people_names: list[str] | None = Field(
        default=None,
        description="Key people names extracted for search (optional).",
    )
    product_names: list[str] | None = Field(
        default=None,
        description="Product names extracted for search (optional).",
    )

    skills: list[str] | None = Field(
        default=None,
        description="Skill keywords for person clients (optional).",
    )
    work_history_companies: list[str] | None = Field(
        default=None,
        description="Company names from work history (optional).",
    )
    work_history_titles: list[str] | None = Field(
        default=None,
        description="Job titles from work history (optional).",
    )
    educational_institutions: list[str] | None = Field(
        default=None,
        description="Institution names from education history (optional).",
    )

    address_cities: list[str] | None = Field(
        default=None,
        description="List of city names extracted from the client's addresses.",
    )
    address_states: list[str] | None = Field(
        default=None,
        description="List of state/region values extracted from the client's addresses.",
    )
    address_countries: list[str] | None = Field(
        default=None,
        description="List of country values extracted from the client's addresses.",
    )
    address_postal_codes: list[str] | None = Field(
        default=None,
        description="List of postal/ZIP codes extracted from the client's addresses.",
    )

    lead_status: str | None = Field(
        default=None,
        description="Lead/pipeline status (optional).",
    )
    lead_score: int | None = Field(
        default=None,
        description="Lead score used for sorting/filtering (optional). Must be an int from DB.",
    )
    intake_stage: str | None = Field(
        default=None,
        description="Intake stage in the pipeline (optional).",
    )

    custom_field_values: list[str] | None = Field(
        default=None,
        description="Flattened custom-field values as strings for search (optional).",
    )
    custom_field_keys: list[str] | None = Field(
        default=None,
        description="Flattened custom-field keys for filtering/diagnostics (optional).",
    )

    enrichment_done: bool | None = Field(
        default=None,
        description="Whether enrichment has been completed (optional).",
    )
    embedding: list[float] | None = Field(
        default=None,
        description="Vector embedding used for semantic search (optional).",
    )

    created_at: int = Field(description="Created timestamp as Unix epoch seconds.")
    updated_at: int = Field(description="Updated timestamp as Unix epoch seconds.")
    company_id: str | None = Field(
        default=None,
        description="Linked company UUID for person clients (display-only, optional).",
    )
    profile_photo_url: str | None = Field(
        default=None,
        description="Client profile image URL (stored for display-only responses).",
    )


class TypesenseContactDocument(BaseModel):
    """Document shape for *contact/person* docs indexed by contacts services.

    This is intentionally narrower than `TypesenseClientDocument` so contact
    indexing stays clean and avoids accidentally coupling to company-only fields.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str
    organization_id: str
    status: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str
    title: str | None = None

    email: str | None = None
    phone_numbers: list[str] | None = None
    phones_display: list[TypesensePhoneEntry] | None = None
    tags: list[str] | None = None

    skills: list[str] | None = None
    work_history_companies: list[str] | None = None
    work_history_titles: list[str] | None = None
    educational_institutions: list[str] | None = None
    educational_degrees: list[str] | None = None
    social_urls: list[str] | None = None
    websites: list[str] | None = None
    address_cities: list[str] | None = None
    address_states: list[str] | None = None
    address_countries: list[str] | None = None
    address_postal_codes: list[str] | None = None

    company_ids: list[str] | None = None
    company_names: list[str] | None = None

    custom_field_keys: list[str] | None = None
    custom_field_values: list[str] | None = None

    enrichment_done: bool | None = None
    created_at: int
    updated_at: int

    profile_photo_url: str | None = None


class TypesenseCompanyDocument(BaseModel):
    """Document shape for *company* docs indexed by companies services."""

    model_config = ConfigDict(extra="ignore", strict=True)

    id: str
    organization_id: str
    status: str | None = None
    name: str

    industry: str | None = None
    email: str | None = None
    phone_numbers: list[str] | None = None
    phones_display: list[TypesensePhoneEntry] | None = None
    contacts: list[dict[str, Any]] | None = None
    contact_full_names: list[str] | None = None
    contact_titles: list[str] | None = None
    contact_emails: list[str] | None = None
    contact_phone_numbers: list[str] | None = None
    tags: list[str] | None = None

    description: str | None = None
    target_market_segments: list[str] | None = None
    current_tech_stack: list[str] | None = None
    preferred_communication_channels: list[str] | None = None
    industry_specific_terminologies: list[str] | None = None
    address_cities: list[str] | None = None
    address_states: list[str] | None = None
    address_countries: list[str] | None = None
    address_postal_codes: list[str] | None = None

    key_people_names: list[str] | None = None
    product_names: list[str] | None = None

    custom_field_keys: list[str] | None = None
    custom_field_values: list[str] | None = None

    enrichment_done: bool | None = None
    created_at: int
    updated_at: int

    profile_photo_url: str | None = None
