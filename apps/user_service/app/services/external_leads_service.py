"""Service-layer orchestration for external (integration) lead operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg
from supabase import AsyncClient

from apps.user_service.app.schemas.contacts import CreateContactRequestStandalone
from apps.user_service.app.schemas.enums import (
    ClientEventType,
    KafkaTopics,
    LeadEventType,
)
from apps.user_service.app.schemas.leads import (
    CreateLeadCompany,
    CreateLeadRequest,
    LeadContactCreate,
)
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.lead_service import LeadService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ConflictException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


@dataclass(frozen=True, slots=True)
class ExternalLeadCreateResult:
    """External lead create result."""

    created: dict[str, Any]
    lead_payload: CreateLeadRequest
    created_contact_id: str | None
    created_company_id: str | None
    contact_created_events: list[tuple[dict, str]]
    lead_created_event: dict | None
    lead_event_key: str | None
    contact_result: dict[str, Any] | None


class ExternalLeadsService:
    """Orchestrates external lead creation flows.

    This service contains the business flow for:
    - validating required contact linkage rules for external create:
      - when no inline contact create payload is provided, at least one existing contact must be
        linked via `lead.contacts`
      - when an inline contact create payload is provided, it will be created/reused and linked,
        and `lead.contacts` (if any) are preserved
    - optionally creating a contact (and related entities)
    - linking the created/reused contact to the lead payload (without overwriting existing links)
    - linking a company created with the inline contact to the lead (``lead.company``)
    - creating lifecycle events (DB rows) for created entities
    """

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
        supabase_client: AsyncClient,
        client_kafka_topics: list[KafkaTopics],
        lead_kafka_topics: list[KafkaTopics],
        organization_id: str,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.supabase_client = supabase_client
        self.client_kafka_topics = client_kafka_topics
        self.lead_kafka_topics = lead_kafka_topics
        self.organization_id = organization_id

        self.lead_service = LeadService(
            user_context=user_context,
            db_connection=db_connection,
        )
        self.contacts_service = ContactsService(
            db_connection=db_connection,
            user_context=user_context,
            supabase_client=supabase_client,
        )
        self.event_service = EventService(db_connection=db_connection)

    def _validate_contact_required_when_no_inline_contact(
        self, lead_payload: CreateLeadRequest
    ) -> None:
        """Require at least one linked contact when no inline contact create payload is provided."""
        provided_contacts = list(lead_payload.contacts or [])
        if not provided_contacts or not any(
            (c.contact_id or "").strip() for c in provided_contacts
        ):
            raise ValidationException(
                message_key="leads.errors.contact_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    async def _create_or_reuse_contact(
        self, contact_payload: CreateContactRequestStandalone
    ) -> tuple[dict[str, Any], str, str | None]:
        """Create a contact or reuse an existing one."""
        try:
            contact_result = await self.contacts_service.create_contact(contact_payload)
            created_contact_id = str(contact_result["contact_id"])
            created_company_id = (
                str(contact_result["company_id"]) if contact_result.get("company_id") else None
            )
            return contact_result, created_contact_id, created_company_id
        except ConflictException as exc:
            # Reuse contact id returned by ContactsService on duplicate email.
            if getattr(exc, "message_key", None) != "contacts.errors.email_already_exists":
                raise
            conflict_contact_id = str((getattr(exc, "params", None) or {}).get("client_id") or "")
            if not conflict_contact_id:
                raise
            return (
                {
                    "contact_id": conflict_contact_id,
                    "reused_existing": True,
                },
                conflict_contact_id,
                None,
            )

    async def _create_contact_lifecycle_events(
        self, contact_result: dict[str, Any] | None
    ) -> list[tuple[dict, str]]:
        """Create lifecycle events for contact creation."""
        contact_created_events: list[tuple[dict, str]] = []
        for entity in (contact_result.get("created_entities") or []) if contact_result else []:
            entity_id = entity.get("entity_id")
            if not entity_id:
                continue
            lifecycle_event = await self.event_service.create_lifecycle_event(
                event_type=ClientEventType.CREATED.value,
                aggregate_id=str(entity_id),
                organization_id=self.organization_id,
                actor_user_id=None,
                payload={"module": "contacts", "action": entity.get("action") or "create"},
                topics=self.client_kafka_topics,
            )
            if lifecycle_event is not None:
                contact_created_events.append((lifecycle_event, str(entity_id)))
        return contact_created_events

    def _ensure_lead_linked_to_contact(
        self,
        lead_payload: CreateLeadRequest,
        *,
        created_contact_id: str,
        lead_contact_label: str | None,
    ) -> None:
        """Ensure the lead is linked to the newly created contact."""
        existing_contacts: list[LeadContactCreate] = list(lead_payload.contacts or [])
        if not any((c.contact_id or "").strip() == created_contact_id for c in existing_contacts):
            existing_contacts.append(
                LeadContactCreate(
                    contact_id=created_contact_id,
                    label=(lead_contact_label.strip() if lead_contact_label else None),
                )
            )
        lead_payload.contacts = existing_contacts or None

    def _ensure_lead_linked_to_company(
        self,
        lead_payload: CreateLeadRequest,
        *,
        created_company_id: str,
    ) -> None:
        """Ensure the lead is linked to the company created with the inline contact."""
        company_id = (created_company_id or "").strip()
        if not company_id:
            return
        existing = lead_payload.company
        if existing is not None:
            existing_id = (existing.company_id or "").strip()
            if existing_id == company_id:
                return
            if existing_id:
                return
        lead_payload.company = CreateLeadCompany(company_id=company_id)

    async def _create_lead_created_lifecycle_event(
        self, created: dict[str, Any]
    ) -> tuple[dict | None, str | None]:
        """Create a lifecycle event for lead creation."""
        lead_created_event: dict | None = None
        lead_event_key: str | None = None
        if isinstance(created, dict) and created.get("id") is not None:
            lead_created_event = await self.event_service.create_lifecycle_event(
                event_type=LeadEventType.CREATED.value,
                aggregate_id=str(created["id"]),
                organization_id=self.organization_id,
                actor_user_id=None,
                payload={"module": "leads", "action": "create"},
                topics=self.lead_kafka_topics,
            )
            lead_event_key = str(created["id"])
        return lead_created_event, lead_event_key

    async def create_lead_with_optional_contact(
        self,
        *,
        lead: CreateLeadRequest,
        contact: CreateContactRequestStandalone | None,
        lead_contact_label: str | None,
    ) -> ExternalLeadCreateResult:
        """Create a lead with an optional contact."""
        created_contact_id: str | None = None
        created_company_id: str | None = None
        contact_created_events: list[tuple[dict, str]] = []
        contact_result: dict[str, Any] | None = None

        lead_payload = lead.model_copy(deep=True)

        if contact is None:
            self._validate_contact_required_when_no_inline_contact(lead_payload)
        else:
            contact_payload = contact.model_copy(deep=True)
            (
                contact_result,
                created_contact_id,
                created_company_id,
            ) = await self._create_or_reuse_contact(contact_payload)

            # Create lifecycle events only when the contact flow actually created entities.
            contact_created_events = await self._create_contact_lifecycle_events(contact_result)

            # Ensure the lead is linked to the newly created contact.
            self._ensure_lead_linked_to_contact(
                lead_payload,
                created_contact_id=created_contact_id,
                lead_contact_label=lead_contact_label,
            )
            if created_company_id:
                self._ensure_lead_linked_to_company(
                    lead_payload,
                    created_company_id=created_company_id,
                )

        created = await self.lead_service.create_lead(lead_payload, external=True)

        lead_created_event, lead_event_key = await self._create_lead_created_lifecycle_event(
            created
        )

        return ExternalLeadCreateResult(
            created=created,
            lead_payload=lead_payload,
            created_contact_id=created_contact_id,
            created_company_id=created_company_id,
            contact_created_events=contact_created_events,
            lead_created_event=lead_created_event,
            lead_event_key=lead_event_key,
            contact_result=contact_result,
        )
