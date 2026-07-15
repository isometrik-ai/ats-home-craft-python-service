"""Schemas for external leads endpoints.

These models are used by `api/external_leads.py` (Isometrik credential auth).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.contacts import CreateContactRequestStandalone
from apps.user_service.app.schemas.leads import CreateLeadRequest
from libs.shared_utils.http_exceptions import BadRequestException


class ExternalCreateLeadRequest(BaseModel):
    """External lead create payload wrapper (external auth).

    Supports:
    - linking existing contacts via `lead.contacts` (same as internal), and/or
    - creating a new contact via `contact` (auto-linked to the created lead).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    lead: CreateLeadRequest = Field(
        ...,
        description=(
            "Lead create payload (same as internal). Use `lead.contacts` to associate existing "
            "contacts by id. Use top-level `contact` to create a new contact (auto-linked)."
        ),
    )
    create_contact: CreateContactRequestStandalone | None = Field(
        default=None,
        description="Optional contact create payload.",
        alias="contact",
    )
    created_contact_label: str | None = Field(
        default=None,
        max_length=255,
        description="Optional label for the created contact on the lead (lead_contacts.label).",
        alias="lead_contact_label",
    )

    @model_validator(mode="after")
    def validate_contact_create_vs_link_existing(self) -> "ExternalCreateLeadRequest":
        """External-only constraint validations for contact inputs."""
        has_contact_create = self.create_contact is not None

        # `created_contact_label` is only meaningful when we're creating a new contact.
        if self.created_contact_label is not None and not has_contact_create:
            raise BadRequestException(
                message_key="leads.errors.external_lead_contact_label_requires_contact",
                errors=[
                    {
                        "field": "body.created_contact_label",
                        "type": "bad_request",
                        "msg": "Requires body.create_contact to be set.",
                    }
                ],
            )

        return self
