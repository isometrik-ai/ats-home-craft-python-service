"""Shared API response envelopes for OpenAPI documentation."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from libs.shared_utils.status_codes import CustomStatusCode

T = TypeVar("T")


class ApiSuccessEnvelope(BaseModel):
    """Standard success envelope returned by response_factory helpers."""

    status: str = Field(..., examples=["success"])
    message: str
    statusCode: int
    code: CustomStatusCode


class DataApiResponse(ApiSuccessEnvelope, Generic[T]):
    """Success envelope with a single `data` payload."""

    data: T


class ListApiResponse(ApiSuccessEnvelope, Generic[T]):
    """Paginated list envelope returned by list_response()."""

    data: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class DeleteIdData(BaseModel):
    """Identifier payload returned after a delete mutation."""

    id: str


class SchedulerRunData(BaseModel):
    """Counts produced by a scheduler run."""

    created: int = 0
    cancelled: int = 0
    skipped: int = 0


class TriggerTestData(BaseModel):
    """Result of a test webhook delivery."""

    delivered: bool
    status: int | None = None
    error: str | None = None
    duration_ms: int


class ItemsTotalData(BaseModel, Generic[T]):
    """Generic list payload with optional total count."""

    items: list[T]
    total: int | None = None


class TimelineEventResponse(BaseModel):
    """Timeline event stored on work orders and invoices."""

    model_config = ConfigDict(extra="forbid")

    id: str
    at: str
    type: str
    note: str | None = None
    by: str | None = None


TimelineListData = list[TimelineEventResponse]
