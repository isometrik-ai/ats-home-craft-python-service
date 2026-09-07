"""Integration request schemas (triggers, API keys)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from apps.work_order_service.app.schemas.enums import TriggerEntity, TriggerEvent


class CreateTriggerRequest(BaseModel):
    """Request body for create trigger operations."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    entity: TriggerEntity
    event: TriggerEvent
    webhook_url: str = Field(..., min_length=1, max_length=2000)
    is_active: bool = True
    secret: str | None = Field(None, max_length=500)


class UpdateTriggerRequest(BaseModel):
    """Request body for update trigger operations."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    entity: TriggerEntity | None = None
    event: TriggerEvent | None = None
    webhook_url: str | None = Field(None, min_length=1, max_length=2000)
    is_active: bool | None = None
    secret: str | None = Field(None, max_length=500)


class CreateApiKeyRequest(BaseModel):
    """Request body for create api key operations."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="API Key", min_length=1, max_length=255)
