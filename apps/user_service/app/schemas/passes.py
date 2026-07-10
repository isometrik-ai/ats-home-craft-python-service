"""Visitor passes schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.enums import (
    PassListBucket,
    PassType,
    PassValidityType,
)


class CreatePassRequest(BaseModel):
    """Create a visitor pass for a guest."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    pass_type: PassType = PassType.GUEST
    guest_name: str = Field(..., min_length=1, max_length=200)
    guest_phone_isd_code: str | None = Field(None, max_length=10)
    guest_phone_number: str | None = Field(None, max_length=20)
    visitor_count: int = Field(1, ge=1, le=50)
    vehicle_number: str | None = Field(None, max_length=20)
    purpose: str | None = Field(None, max_length=500)
    valid_from: datetime
    valid_until: datetime
    validity_type: PassValidityType = PassValidityType.ONE_TIME
    allow_multiple_entries: bool = False
    is_private: bool = False
    max_entries: int | None = Field(None, ge=1)
    notes: str | None = Field(None, max_length=1000)

    @field_validator("valid_until")
    @classmethod
    def valid_until_after_from(cls, valid_until: datetime, info) -> datetime:
        """Ensure valid_until is after valid_from."""
        valid_from = info.data.get("valid_from")
        if valid_from is not None and valid_until <= valid_from:
            raise ValueError("valid_until must be after valid_from")
        return valid_until


class UpdatePassRequest(BaseModel):
    """Patch an upcoming or active pass."""

    model_config = ConfigDict(extra="forbid")

    pass_type: PassType | None = None
    guest_name: str | None = Field(None, min_length=1, max_length=200)
    guest_phone_isd_code: str | None = Field(None, max_length=10)
    guest_phone_number: str | None = Field(None, max_length=20)
    visitor_count: int | None = Field(None, ge=1, le=50)
    vehicle_number: str | None = Field(None, max_length=20)
    purpose: str | None = Field(None, max_length=500)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    validity_type: PassValidityType | None = None
    allow_multiple_entries: bool | None = None
    is_private: bool | None = None
    max_entries: int | None = Field(None, ge=1)
    notes: str | None = Field(None, max_length=1000)


class PassEventResponse(BaseModel):
    """Single pass timeline event."""

    model_config = ConfigDict(extra="ignore")

    id: str
    event_type: str
    gate_id: str | None = None
    actor_type: str | None = None
    actor_user_id: str | None = None
    actor_label: str | None = None
    occurred_at: str | None = None
    notes: str | None = None
    metadata: dict | None = None


class PassResponse(BaseModel):
    """Pass details returned to the resident."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    unit_id: str
    host_contact_id: str
    pass_type: str
    guest_name: str
    guest_phone_isd_code: str | None = None
    guest_phone_number: str | None = None
    visitor_count: int
    vehicle_number: str | None = None
    purpose: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    validity_type: str
    allow_multiple_entries: bool
    is_private: bool = False
    max_entries: int | None = None
    entry_count: int
    status: str
    display_status: str
    code: str
    pass_image_path: str | None = None
    notes: str | None = None
    unit_code: str | None = None
    unit_label: str | None = None
    tower_name: str | None = None
    floor_name: str | None = None
    config_label: str | None = None
    events: list[PassEventResponse] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PassListItemResponse(BaseModel):
    """Pass summary for list views."""

    model_config = ConfigDict(extra="ignore")

    id: str
    code: str
    guest_name: str
    pass_type: str
    unit_id: str
    unit_label: str | None = None
    tower_name: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    status: str
    display_status: str
    entry_count: int
    is_private: bool = False


class PassListQuery(BaseModel):
    """Query params for listing passes."""

    model_config = ConfigDict(extra="forbid")

    bucket: PassListBucket | None = None
    unit_id: str | None = None
    pass_type: PassType | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
