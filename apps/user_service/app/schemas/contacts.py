"""Contacts schemas aligned with public.contacts."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.common import (
    Email,
    NoteItem,
    Phone,
    SocialPage,
    Website,
)
from apps.user_service.app.schemas.enums import ClientStatus, ContactType
from apps.user_service.app.schemas.list_filters import DropdownCustomFieldFilter
from apps.user_service.app.utils.common_utils import parse_flexible_date
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

FlexibleOptionalDate = Annotated[date | None, BeforeValidator(parse_flexible_date)]


def _validate_exactly_one_primary_phone(phones: list[Phone]) -> list[Phone]:
    """Validate exactly one primary phone."""
    primary_count = sum(1 for phone in phones if phone.is_primary)
    if primary_count != 1:
        raise ValidationException(
            message_key="contacts.errors.exactly_one_primary_phone",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    return phones


class CreateContactRequest(BaseModel):
    """Create a contact (public.contacts)."""

    model_config = ConfigDict(extra="forbid")

    contact_type: ContactType = Field(..., description="Contact type (Owner, Tenant, etc.).")
    portal_access: bool = Field(
        default=True,
        description="If true, provisions auth user + Isometrik identity for portal access.",
    )

    prefix: str | None = Field(None, max_length=50)
    first_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    title: str | None = Field(None, max_length=100)
    date_of_birth: FlexibleOptionalDate = None
    profile_photo_url: str | None = Field(None, max_length=500)

    emails: list[Email] = Field(default_factory=list, max_length=20)
    phones: list[Phone] = Field(..., min_length=1, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=50)
    social_pages: list[SocialPage] = Field(default_factory=list, max_length=20)
    websites: list[Website] = Field(default_factory=list, max_length=10)
    documents: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Document metadata stored in contacts.documents jsonb.",
    )
    description: str | None = Field(None, max_length=5000)

    custom_fields: list[dict[str, Any]] = Field(default_factory=list)
    additional_data: dict[str, Any] = Field(default_factory=dict)
    notes: list[NoteItem] = Field(default_factory=list)

    @field_validator("phones")
    @classmethod
    def validate_primary_phone(cls, phones: list[Phone]) -> list[Phone]:
        """Validate exactly one primary phone."""
        return _validate_exactly_one_primary_phone(phones)


class UpdateContactRequest(BaseModel):
    """Patch a contact."""

    model_config = ConfigDict(extra="forbid")

    emails: list[Email] | None = None
    contact_type: ContactType | None = None
    status: ClientStatus | None = None
    prefix: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    date_of_birth: FlexibleOptionalDate = None
    profile_photo_url: str | None = None
    phones: list[Phone] | None = None
    tags: list[str] | None = None
    social_pages: list[SocialPage] | None = None
    websites: list[Website] | None = None
    documents: list[dict[str, Any]] | None = None
    description: str | None = None
    custom_fields: list[dict[str, Any]] | None = None
    additional_data: dict[str, Any] | None = None
    notes: list[NoteItem] | None = None

    @field_validator("phones")
    @classmethod
    def validate_primary_phone(cls, phones: list[Phone] | None) -> list[Phone] | None:
        """Validate exactly one primary phone."""
        if phones is None:
            return phones
        return _validate_exactly_one_primary_phone(phones)


class ListContactsRequest(BaseModel):
    """Request body for listing contacts."""

    model_config = ConfigDict(extra="forbid")

    search: str | None = Field(default=None, min_length=2)
    status: ClientStatus | None = None
    contact_type: ContactType | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    dropdown_filters: list[DropdownCustomFieldFilter] = Field(default_factory=list)


class ContactSummaryResponse(BaseModel):
    """List row for a contact."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    status: str
    contact_type: str
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    profile_photo_url: str | None = None
    phones: list[Any] = Field(default_factory=list)
    emails: list[Any] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ContactDetailsResponse(BaseModel):
    """Full contact details."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    user_id: str | None = None
    isometrik_user_id: str | None = None
    status: str
    contact_type: str

    prefix: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    date_of_birth: str | None = None
    profile_photo_url: str | None = None

    phones: list[Any] = Field(default_factory=list)
    emails: list[Any] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    custom_fields: list[Any] = Field(default_factory=list)
    additional_data: dict[str, Any] = Field(default_factory=dict)
    social_pages: Any = Field(default_factory=list)
    documents: list[Any] = Field(default_factory=list)
    description: str | None = None
    websites: list[Any] = Field(default_factory=list)
    notes: list[Any] = Field(default_factory=list)

    created_at: str
    updated_at: str
