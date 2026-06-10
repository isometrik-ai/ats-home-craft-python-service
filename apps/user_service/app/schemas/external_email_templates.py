"""Schemas for `/integrations/email-templates/*` endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.email_templates import CreateEmailTemplateRequest


class ExternalCreateEmailTemplateRequest(CreateEmailTemplateRequest):
    """External create payload scoped by explicit organization id."""

    organization_id: str = Field(..., min_length=1, description="Target organization UUID")


class ExternalCreateEmailTemplateResult(BaseModel):
    """External create response for email template create."""

    model_config = ConfigDict(extra="forbid")

    template_id: str
    name: str
    template_type: str
    status: str


__all__ = [
    "ExternalCreateEmailTemplateRequest",
    "ExternalCreateEmailTemplateResult",
]
