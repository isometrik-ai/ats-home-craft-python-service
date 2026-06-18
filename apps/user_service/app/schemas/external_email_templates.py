"""Schemas for `/integrations/email-templates/*` endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ExternalCreateEmailTemplateResult(BaseModel):
    """External create response for email template create."""

    model_config = ConfigDict(extra="forbid")

    template_id: str
    name: str
    template_type: str
    status: str


__all__ = [
    "ExternalCreateEmailTemplateResult",
]
