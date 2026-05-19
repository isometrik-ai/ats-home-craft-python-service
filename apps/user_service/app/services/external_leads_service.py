"""Service-layer orchestration for external (integration) lead operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg
from fastapi import BackgroundTasks, Request
from supabase import AsyncClient

from apps.user_service.app.schemas.companies import (
    CreateCompanyRequest,
    CreateCompanyRequestStandalone,
)
from apps.user_service.app.schemas.contacts import CreateContactRequestStandalone
from apps.user_service.app.schemas.enums import KafkaTopics, LeadEventType
from apps.user_service.app.schemas.leads import (
    CreateLeadCompany,
    CreateLeadRequest,
    LeadContactCreate,
)
from apps.user_service.app.services.companies_service import CompaniesService
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.lead_service import LeadService
from apps.user_service.app.services.typesense_index_service import (
    index_contacts_background,
)
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
    lead_company_id: str | None
    contact_created_events: list[tuple[dict, str]]
    lead_created_event: dict | None
    lead_event_key: str | None
    contact_result: dict[str, Any] | None
    company_result: dict[str, Any] | None


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
    - optionally creating a company linked on the lead only
      (``create_company`` → ``lead_companies``)
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
        self.companies_service = CompaniesService(
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

    async def _prepare_inline_contact_create(
        self,
        lead_payload: CreateLeadRequest,
        *,
        contact: CreateContactRequestStandalone,
        lead_contact_label: str | None,
        actor_user_id: str | None,
    ) -> tuple[dict[str, Any] | None, str, str | None, list[tuple[dict, str]]]:
        """Create/reuse contact, persist lifecycle rows, and link entities on the lead payload."""
        (
            contact_result,
            created_contact_id,
            created_company_id,
        ) = await self._create_or_reuse_contact(contact.model_copy(deep=True))
        contact_created_events = await ContactsService.create_lifecycle_events_for_created_entities(
            event_service=self.event_service,
            created_entities=contact_result.get("created_entities") if contact_result else None,
            organization_id=self.organization_id,
            actor_user_id=actor_user_id,
        )
        self._ensure_lead_linked_to_contact(
            lead_payload,
            created_contact_id=created_contact_id,
            lead_contact_label=lead_contact_label,
        )
        if created_company_id:
            self._ensure_lead_linked_to_company(
                lead_payload,
                company_id=created_company_id,
            )
        return contact_result, created_contact_id, created_company_id, contact_created_events

    async def _prepare_lead_only_company_create(
        self,
        lead_payload: CreateLeadRequest,
        *,
        company: CreateCompanyRequestStandalone,
        lead_company_label: str | None,
        actor_user_id: str | None,
    ) -> tuple[str, dict[str, Any], list[tuple[dict, str]]]:
        """Create a company and link it on the lead only (no ``contact_companies`` row)."""
        company_body = CreateCompanyRequest.model_validate(company.model_dump())
        company_result = await self.companies_service.create_company(company_body)
        lead_company_id = str(company_result["company_id"])
        company_events = await CompaniesService.create_lifecycle_events_for_created_entities(
            event_service=self.event_service,
            created_entities=company_result.get("created_entities"),
            organization_id=self.organization_id,
            actor_user_id=actor_user_id,
        )
        # Lead-only company: set explicit ``lead.company`` (may differ from a
        # contact-linked company, still on lead via ``contact_companies`` auto-link).
        lead_payload.company = CreateLeadCompany(
            company_id=lead_company_id,
            label=(lead_company_label.strip() if lead_company_label else None),
        )
        return lead_company_id, company_result, company_events

    def _ensure_lead_linked_to_company(
        self,
        lead_payload: CreateLeadRequest,
        *,
        company_id: str,
        label: str | None = None,
    ) -> None:
        """Ensure the lead is linked to a company (``lead_companies`` only)."""
        normalized_id = (company_id or "").strip()
        if not normalized_id:
            return
        existing = lead_payload.company
        if existing is not None:
            existing_id = (existing.company_id or "").strip()
            if existing_id == normalized_id:
                return
            if existing_id:
                return
        lead_payload.company = CreateLeadCompany(
            company_id=normalized_id,
            label=(label.strip() if label else None),
        )

    async def _create_lead_created_lifecycle_event(
        self,
        created: dict[str, Any],
        *,
        actor_user_id: str | None = None,
    ) -> tuple[dict | None, str | None]:
        """Create a lifecycle event for lead creation."""
        lead_created_event: dict | None = None
        lead_event_key: str | None = None
        if isinstance(created, dict) and created.get("id") is not None:
            lead_created_event = await self.event_service.create_lifecycle_event(
                event_type=LeadEventType.CREATED.value,
                aggregate_id=str(created["id"]),
                organization_id=self.organization_id,
                actor_user_id=actor_user_id,
                payload={"module": "leads", "action": "create"},
                topics=self.lead_kafka_topics,
            )
            lead_event_key = str(created["id"])
        return lead_created_event, lead_event_key

    @staticmethod
    def apply_create_audit_state(
        request: Request,
        *,
        result: ExternalLeadCreateResult,
        user_context: UserContext,
        external_actor: bool = False,
    ) -> None:
        """Populate audit fields on ``request.state`` after a lead create."""
        created = result.created
        lead_payload = result.lead_payload
        request.state.audit_table = "leads"
        request.state.audit_requested_id = (
            str(created.get("id", "")) if isinstance(created, dict) else ""
        )
        if result.created_contact_id is not None:
            audit_description = f"Created lead with new contact: {lead_payload.name!r}"
        elif result.lead_company_id is not None:
            audit_description = f"Created lead with new company: {lead_payload.name!r}"
        else:
            audit_description = f"Created lead: {lead_payload.name!r}"
        request.state.audit_description = audit_description
        request.state.audit_risk_level = (
            "high"
            if result.created_contact_id is not None or result.lead_company_id is not None
            else "medium"
        )
        request.state.audit_user_context = {
            "user_id": (
                "00000000-0000-0000-0000-000000000000" if external_actor else user_context.user_id
            ),
            "user_email": user_context.email,
            "organization_id": user_context.organization_id,
        }
        request.state.raw_audit_new_data = (
            LeadService._normalize_lead_audit_snapshot(created)
            if isinstance(created, dict)
            else created
        )
        if result.created_contact_id is not None or result.lead_company_id is not None:
            request.state.raw_audit_new_data = {
                "lead": request.state.raw_audit_new_data,
                "created_contact_id": result.created_contact_id,
                "created_company_id": result.created_company_id,
                "lead_company_id": result.lead_company_id,
            }

    @staticmethod
    def _collect_created_entities(result: ExternalLeadCreateResult) -> list[dict[str, Any]]:
        """Merge ``created_entities`` from inline contact and lead-only company creates."""
        entities: list[dict[str, Any]] = []
        if result.contact_result:
            entities.extend(result.contact_result.get("created_entities") or [])
        if result.company_result:
            entities.extend(result.company_result.get("created_entities") or [])
        return entities

    @staticmethod
    def _collect_enrichment_targets(result: ExternalLeadCreateResult) -> list[dict[str, Any]]:
        """Merge enrichment targets from contact and lead-only company creates."""
        targets: list[dict[str, Any]] = []
        if result.contact_result:
            targets.extend(result.contact_result.get("enrichment_targets") or [])
        if result.company_result:
            targets.extend(result.company_result.get("enrichment_targets") or [])
        return targets

    @staticmethod
    def build_create_response_data(result: ExternalLeadCreateResult) -> dict[str, Any]:
        """Build optional response ids for lead create (external API)."""
        created = result.created
        response_data: dict[str, Any] = (
            {"lead_id": str(created.get("id"))} if isinstance(created, dict) else {}
        )
        if result.created_contact_id is not None:
            response_data["contact_id"] = result.created_contact_id
        if result.lead_company_id is not None:
            response_data["company_id"] = result.lead_company_id
        elif result.created_company_id is not None:
            response_data["company_id"] = result.created_company_id
        if (
            result.created_company_id is not None
            and result.lead_company_id is not None
            and result.created_company_id != result.lead_company_id
        ):
            response_data["contact_company_id"] = result.created_company_id
        return response_data

    @staticmethod
    def schedule_create_post_commit(
        background_tasks: BackgroundTasks,
        *,
        result: ExternalLeadCreateResult,
        organization_id: str,
        lead_kafka_topics: list[KafkaTopics],
    ) -> None:
        """Publish lifecycle events and run indexing/enrichment after commit."""
        created_entities = ExternalLeadsService._collect_created_entities(result)

        ContactsService.schedule_lifecycle_event_publishes(
            background_tasks=background_tasks,
            created_events=result.contact_created_events,
        )
        if result.lead_created_event is not None and result.lead_event_key is not None:
            background_tasks.add_task(
                EventService.publish_event_background,
                event=result.lead_created_event,
                key=result.lead_event_key,
                topics=lead_kafka_topics,
            )
        ContactsService.schedule_typesense_indexing_for_created_entities(
            background_tasks=background_tasks,
            created_entities=created_entities or None,
            organization_id=organization_id,
        )
        if result.created_contact_id and not any(
            e.get("entity_table") == "contacts" and e.get("entity_id") == result.created_contact_id
            for e in created_entities
        ):
            background_tasks.add_task(
                index_contacts_background,
                [(result.created_contact_id, organization_id)],
            )
        enrichment_targets = ExternalLeadsService._collect_enrichment_targets(result)
        if enrichment_targets:
            ContactsService.schedule_enrichment(
                background_tasks=background_tasks,
                enrichment_targets=enrichment_targets,
            )

    async def create_lead_with_optional_contact(
        self,
        *,
        lead: CreateLeadRequest,
        contact: CreateContactRequestStandalone | None,
        lead_contact_label: str | None,
        create_company: CreateCompanyRequestStandalone | None = None,
        lead_company_label: str | None = None,
        external: bool = True,
        require_linked_contact: bool = True,
        actor_user_id: str | None = None,
    ) -> ExternalLeadCreateResult:
        """Create a lead with an optional contact."""
        created_contact_id: str | None = None
        created_company_id: str | None = None
        lead_company_id: str | None = None
        contact_created_events: list[tuple[dict, str]] = []
        contact_result: dict[str, Any] | None = None
        company_result: dict[str, Any] | None = None

        lead_payload = lead.model_copy(deep=True)

        if contact is None and require_linked_contact:
            self._validate_contact_required_when_no_inline_contact(lead_payload)
        elif contact is not None:
            (
                contact_result,
                created_contact_id,
                created_company_id,
                contact_created_events,
            ) = await self._prepare_inline_contact_create(
                lead_payload,
                contact=contact,
                lead_contact_label=lead_contact_label,
                actor_user_id=actor_user_id,
            )

        if create_company is not None:
            (
                lead_company_id,
                company_result,
                company_events,
            ) = await self._prepare_lead_only_company_create(
                lead_payload,
                company=create_company,
                lead_company_label=lead_company_label,
                actor_user_id=actor_user_id,
            )
            contact_created_events.extend(company_events)

        created = await self.lead_service.create_lead(lead_payload, external=external)

        lead_created_event, lead_event_key = await self._create_lead_created_lifecycle_event(
            created,
            actor_user_id=actor_user_id,
        )

        return ExternalLeadCreateResult(
            created=created,
            lead_payload=lead_payload,
            created_contact_id=created_contact_id,
            created_company_id=created_company_id,
            lead_company_id=lead_company_id,
            contact_created_events=contact_created_events,
            lead_created_event=lead_created_event,
            lead_event_key=lead_event_key,
            contact_result=contact_result,
            company_result=company_result,
        )
