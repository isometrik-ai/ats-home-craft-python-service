"""Lead Stages Schemas Module.

Pydantic models for lead stage create, update, and read operations.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class LeadStageColor(str, Enum):
    """Allowed stage color keys (mapped to UI tokens on frontend)."""

    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    PURPLE = "purple"
    PINK = "pink"
    GRAY = "gray"


class LeadStageBasePayload(BaseModel):
    """Common writable fields for lead stages."""

    stage_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Stage display name (unique per organization)",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="AI-facing stage description; null to clear",
    )
    color: LeadStageColor | None = Field(
        default=None,
        description="UI color key from allowed palette; null to clear",
    )
    sort_order: int | None = Field(
        default=None,
        ge=1,
        description="Pipeline position (1..N or 1..N+1 based on operation)",
    )
    is_initial: bool | None = Field(
        default=None,
        description="Whether this stage is an entry stage",
    )
    is_final: bool | None = Field(
        default=None,
        description="Whether this stage is a terminal won/closed stage",
    )

    @field_validator("stage_name")
    @classmethod
    def validate_stage_name_not_blank(cls, value: str | None) -> str | None:
        """Disallow whitespace-only stage names."""
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValidationException(
                message_key="lead_stages.errors.stage_name_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        """Normalize blank description strings to None."""
        if value is None:
            return value
        normalized = value.strip()
        return normalized or None


class CreateLeadStageRequest(LeadStageBasePayload):
    """Request schema for creating a lead stage."""

    model_config = ConfigDict(extra="forbid")

    stage_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Stage display name (unique per organization)",
    )
    is_initial: bool = Field(
        default=False,
        description="Entry-stage flag. First stage is forced to true by service layer.",
    )
    is_final: bool = Field(
        default=False,
        description="Final-stage flag. First stage is forced to true by service layer.",
    )


class UpdateLeadStageRequest(LeadStageBasePayload):
    """Request schema for partially updating a lead stage (PATCH semantics)."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_at_least_one_field(self) -> "UpdateLeadStageRequest":
        """Require at least one mutable field in PATCH payload."""
        if all(
            value is None
            for value in (
                self.stage_name,
                self.description,
                self.color,
                self.sort_order,
                self.is_initial,
                self.is_final,
            )
        ):
            raise ValidationException(
                message_key="lead_stages.errors.empty_update_payload",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class LeadStageResponse(BaseModel):
    """Lead stage response schema."""

    id: str = Field(..., description="Lead stage ID")
    stage_name: str = Field(..., description="Display name")
    stage_key: str = Field(..., description="Stable slug key (immutable)")
    description: str | None = Field(None, description="AI-facing stage description")
    color: LeadStageColor | None = Field(None, description="UI color key")
    sort_order: int = Field(..., ge=1, description="Pipeline order")
    is_initial: bool = Field(..., description="Entry-stage flag")
    is_final: bool = Field(..., description="Final-stage flag")
    created_at: str = Field(..., description="Creation timestamp (ISO 8601)")
    updated_at: str = Field(..., description="Last update timestamp (ISO 8601)")


__all__ = [
    "LeadStageColor",
    "CreateLeadStageRequest",
    "UpdateLeadStageRequest",
    "LeadStageResponse",
]
