"""Form template request schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateFormTemplateRequest(BaseModel):
    """Request body for create form template operations."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    form_schema: dict[str, Any] = Field(default_factory=dict, alias="schema")


class UpdateFormTemplateRequest(BaseModel):
    """Request body for update form template operations."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    form_schema: dict[str, Any] | None = Field(None, alias="schema")
