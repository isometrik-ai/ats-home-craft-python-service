"""Request/response schemas for organization-scoped CRM memory Q&A."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

EntityTypeFilter = Literal["contact", "company", "lead"]


class OrgMemoryQueryBody(BaseModel):
    """POST body for natural-language CRM overview queries."""

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., min_length=1, max_length=4000)
    entity_id: str | None = Field(
        default=None,
        description="Optional CRM record UUID to scope search (requires entity_type).",
    )
    entity_type: EntityTypeFilter | None = Field(
        default=None,
        description="contact, company, or lead — required when entity_id is set.",
    )

    @model_validator(mode="after")
    def _entity_scope_pair(self) -> OrgMemoryQueryBody:
        """Require entity_type whenever entity_id is provided."""
        has_id = bool(self.entity_id and self.entity_id.strip())
        has_type = self.entity_type is not None
        if has_id != has_type:
            raise ValidationException(
                message_key="errors.validation_error",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"field": "entity_id and entity_type must be provided together"},
            )
        return self


class OrgMemoryQueryResponse(BaseModel):
    """User-facing natural-language reply (no search metadata)."""

    answer: str
