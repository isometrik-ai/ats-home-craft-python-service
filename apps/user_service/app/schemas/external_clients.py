"""Schemas for `/integrations/clients/*` endpoints.

These endpoints reuse the same split-table DTOs as `/companies/*` and `/contacts/*`.

This module only keeps external-specific response shapes that are not part of the
resource APIs (e.g., returning created identifiers).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ExternalCreateCompanyResult(BaseModel):
    """External create response for company create."""

    model_config = ConfigDict(extra="forbid")

    company_id: str
    contact_id: str | None = None
    lead_id: str | None = None


class ExternalCreateContactResult(BaseModel):
    """External create response for contact create."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str
    company_id: str | None = None
    lead_id: str | None = None


__all__ = [
    "ExternalCreateCompanyResult",
    "ExternalCreateContactResult",
]
