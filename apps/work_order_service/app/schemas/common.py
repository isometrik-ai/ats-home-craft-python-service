"""Shared schema types for work_order_service."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TimelineEventRequest(BaseModel):
    """Body for appending a timeline event (work orders, invoices)."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., min_length=1, max_length=100)
    note: str | None = Field(None, max_length=4000)
    by: str | None = Field(None, max_length=255)


def dump_request(model: BaseModel, *, partial: bool = False) -> dict[str, Any]:
    """Serialize a request model for repository/service layers."""
    if partial:
        return model.model_dump(exclude_unset=True, by_alias=True)
    return model.model_dump(by_alias=True)
