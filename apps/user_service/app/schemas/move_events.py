"""Move events schemas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.enums import (
    TENANT_REQUEST_REQUIRED_DOCUMENT_TYPES,
    MoveEventListBucket,
    MoveEventType,
)
from apps.user_service.app.schemas.tenant_requests import TenantRequestDocumentInput


class MoveEventDocumentResponse(BaseModel):
    """Typed document stored on a move event."""

    model_config = ConfigDict(extra="ignore")

    document_type: str
    file_path: str
    file_name: str | None = None
    status: str | None = None


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
    documents: list[TenantRequestDocumentInput] | None = None

    @model_validator(mode="after")
    def validate_documents_for_move_type(self) -> CreateMoveEventRequest:
        """Require typed documents on move-in only."""
        if self.move_type == MoveEventType.MOVE_IN:
            if not self.documents:
                raise ValueError(
                    "documents are required for move_in and must include "
                    "id_proof, rental_agreement, and police_verification"
                )
            provided = {item.document_type for item in self.documents}
            required = set(TENANT_REQUEST_REQUIRED_DOCUMENT_TYPES)
            if provided != required:
                raise ValueError(
                    "documents must include id_proof, rental_agreement, and police_verification"
                )
            return self
        if self.documents:
            raise ValueError("documents may only be supplied for move_in")
        return self


class UpdateMoveEventRequest(BaseModel):
    """Patch move event details (date, fee, notes, documents only)."""

    model_config = ConfigDict(extra="forbid")

    event_date: date | None = None
    fee_amount: Decimal | None = Field(None, ge=0)
    fee_currency: str | None = Field(None, min_length=3, max_length=3)
    notes: str | None = Field(None, max_length=2000)
    documents: list[TenantRequestDocumentInput] | None = None

    @model_validator(mode="after")
    def validate_documents_when_present(self) -> UpdateMoveEventRequest:
        """When documents are patched, require the full move-in document set."""
        if self.documents is None:
            return self
        provided = {item.document_type for item in self.documents}
        required = set(TENANT_REQUEST_REQUIRED_DOCUMENT_TYPES)
        if provided != required:
            raise ValueError(
                "documents must include id_proof, rental_agreement, and police_verification"
            )
        return self


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
    documents: list[MoveEventDocumentResponse] = Field(default_factory=list)
    recorded_by_user_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    unit_code: str | None = None
    unit_label: str | None = None
    unit_tower_name: str | None = None
    unit_type: str | None = None
    contact_name: str | None = None
    contact_role: str | None = None
