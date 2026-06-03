"""AI Overview Settings request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MAX_BUSINESS_OVERVIEW_LEN = 2000
_MAX_OVERVIEW_PROMPT_LEN = 4000


class OverviewPrompts(BaseModel):
    """Overview agent prompts for lead, contact, and company records."""

    model_config = ConfigDict(extra="forbid")

    lead: str = Field(..., description="Agent prompt for lead AI overview")
    contact: str = Field(..., description="Agent prompt for contact AI overview")
    company: str = Field(..., description="Agent prompt for company AI overview")


class AiOverviewSettings(BaseModel):
    """Organization AI Overview configuration."""

    model_config = ConfigDict(extra="forbid")

    business_overview: str | None = Field(
        default=None,
        description="Company facts the AI reads as background (not an instruction prompt)",
    )
    overview_prompts: OverviewPrompts = Field(
        ...,
        description="Agent instructions for each record type's AI Overview",
    )


class OverviewPromptsUpdate(BaseModel):
    """Partial update for overview prompts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    lead: str | None = Field(default=None, max_length=_MAX_OVERVIEW_PROMPT_LEN)
    contact: str | None = Field(default=None, max_length=_MAX_OVERVIEW_PROMPT_LEN)
    company: str | None = Field(default=None, max_length=_MAX_OVERVIEW_PROMPT_LEN)


class AiOverviewSettingsUpdate(BaseModel):
    """PATCH payload for AI Overview Settings on organization update."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    business_overview: str | None = Field(
        default=None,
        max_length=_MAX_BUSINESS_OVERVIEW_LEN,
        description="Company facts for AI background; null or empty clears stored value",
    )
    overview_prompts: OverviewPromptsUpdate | None = Field(
        default=None,
        description=(
            "Per-entity prompts; null on a field resets that prompt to the platform default"
        ),
    )

    @field_validator("business_overview", mode="before")
    @classmethod
    def _normalize_business_overview(cls, value: object) -> object:
        """Strip business overview; empty string becomes None."""
        if value is None or not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped if stripped else None


AiOverviewRefetchField = Literal[
    "business_overview",
    "lead",
    "contact",
    "company",
]


class AiOverviewRefetchBody(BaseModel):
    """Request body to refetch selected AI overview fields for the current organization."""

    model_config = ConfigDict(extra="forbid")

    fields: list[AiOverviewRefetchField] = Field(
        ...,
        min_length=1,
        description=(
            "Fields to refetch independently (no chaining): business_overview, "
            "or individual overview prompts (lead, contact, company). "
            "Prompt refetch requires stored business_overview."
        ),
    )

    @field_validator("fields")
    @classmethod
    def _dedupe_fields(cls, value: list[str]) -> list[str]:
        """Preserve order while removing duplicates."""
        seen: list[str] = []
        for item in value:
            if item not in seen:
                seen.append(item)
        return seen
