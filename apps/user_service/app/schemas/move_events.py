"""Move events schemas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.enums import MoveEventListBucket, MoveEventType


class CreateMoveEventRequest(BaseModel):
    """Record a move-in or move-out event."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    contact_id: str
    move_type: MoveEventType
    event_date: date
    fee_amount: Decimal | None = Field(None, ge=0)
    fee_currency: str = Field(default="INR", min_length=3, max_length=3)
    notes: str | None = Field(None, max_length=2000)
    document_paths: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("document_paths")
    @classmethod
    def validate_document_paths(cls, value: list[str]) -> list[str]:
        """Cap document path count."""
        if len(value) > 20:
            raise ValueError("document_paths cannot exceed 20 items")
        return value


class UpdateMoveEventRequest(BaseModel):
    """Patch move event details (date, fee, notes, documents only)."""

    model_config = ConfigDict(extra="forbid")

    event_date: date | None = None
    fee_amount: Decimal | None = Field(None, ge=0)
    fee_currency: str | None = Field(None, min_length=3, max_length=3)
    notes: str | None = Field(None, max_length=2000)
    document_paths: list[str] | None = Field(None, max_length=20)

    @field_validator("document_paths")
    @classmethod
    def validate_document_paths(cls, value: list[str] | None) -> list[str] | None:
        """Cap document path count."""
        if value is not None and len(value) > 20:
            raise ValueError("document_paths cannot exceed 20 items")
        return value


class MoveEventListQuery(BaseModel):
    """Query params for GET /move-events."""

    model_config = ConfigDict(extra="forbid")

    bucket: MoveEventListBucket | None = None
    search: str | None = Field(None, max_length=200)
    unit_id: str | None = None
    project_id: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class MoveEventResponse(BaseModel):
    """Move event row for list/detail responses."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    unit_id: str
    contact_id: str
    contact_unit_id: str | None = None
    move_type: str
    event_date: str
    fee_amount: str | None = None
    fee_currency: str
    notes: str | None = None
    document_paths: list[str] = Field(default_factory=list)
    recorded_by_user_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    unit_code: str | None = None
    unit_label: str | None = None
    unit_tower_name: str | None = None
    unit_type: str | None = None
    contact_name: str | None = None
    contact_role: str | None = None
