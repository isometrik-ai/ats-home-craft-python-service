"""Schemas for user push token registration."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.enums import PushPlatform


class RegisterUserPushTokenRequest(BaseModel):
    """Register or refresh a push device for the authenticated user."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=1, max_length=128)
    push_token: str = Field(..., min_length=1, max_length=4096)
    platform: PushPlatform
    app_version: str | None = Field(None, max_length=64)

    @field_validator("device_id", "push_token")
    @classmethod
    def strip_required_strings(cls, value: str) -> str:
        """Reject blank strings after trimming."""
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("app_version")
    @classmethod
    def strip_optional_string(cls, value: str | None) -> str | None:
        """Normalize optional string fields."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class UserPushTokenResponse(BaseModel):
    """Public push token registration summary (no push token)."""

    model_config = ConfigDict(extra="ignore")

    device_id: str
    platform: PushPlatform
    registered_at: str
