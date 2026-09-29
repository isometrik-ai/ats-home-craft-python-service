"""Unit tests for ExternalLeadsService orchestration helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from apps.user_service.app.schemas.companies import CreateCompanyRequestStandalone
from apps.user_service.app.schemas.contacts import CreateContactRequestStandalone
from apps.user_service.app.schemas.enums import KafkaTopics
from apps.user_service.app.schemas.leads import (
    CreateLeadCompany,
    CreateLeadRequest,
    LeadContactCreate,
)
from apps.user_service.app.services.external_leads_service import (
    ExternalLeadCreateResult,
    ExternalLeadsService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ConflictException, ValidationException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "770e8400-e29b-41d4-a716-446655440002"
COMPANY_ID = "880e8400-e29b-41d4-a716-446655440003"
LEAD_ID = "990e8400-e29b-41d4-a716-446655440004"


def _ctx() -> UserContext:
    """Build user context for external lead tests."""
    return UserContext(user_id="user-1", email="owner@example.com", organization_id=ORG_ID)


def _lead_payload(**overrides) -> CreateLeadRequest:
    """Build minimal lead payload."""
    data = {"name": "Enterprise Deal", "stage_id": "stage-1"}
    data.update(overrides)
    return CreateLeadRequest(**data)


def _service() -> ExternalLeadsService:
    """Build ExternalLeadsService with mocked collaborators."""
    return ExternalLeadsService(
        db_connection=MagicMock(),
        user_context=_ctx(),
        supabase_client=MagicMock(),
        client_kafka_topics=[KafkaTopics.CRM_EVENTS],
        lead_kafka_topics=[KafkaTopics.CRM_EVENTS],
        organization_id=ORG_ID,
    )


def test_validate_contact_required_when_no_inline_contact():
    """Lead without linked contacts is rejected when inline contact omitted."""
    service = _service()

    with pytest.raises(ValidationException) as exc_info:
        service._validate_contact_required_when_no_inline_contact(_lead_payload(contacts=[]))
    assert exc_info.value.message_key == "leads.errors.contact_required"


def test_ensure_lead_linked_to_contact_appends_new_link():
    """Inline contact id is appended when not already linked."""
    service = _service()
    lead_payload = _lead_payload(
        contacts=[LeadContactCreate(contact_id="existing-contact", label="Primary")]
    )

    service._ensure_lead_linked_to_contact(
        lead_payload,
        created_contact_id=CONTACT_ID,
        lead_contact_label=" Decision Maker ",
    )

    assert len(lead_payload.contacts) == 2
    assert lead_payload.contacts[-1].contact_id == CONTACT_ID
    assert lead_payload.contacts[-1].label == "Decision Maker"


def test_ensure_lead_linked_to_company_sets_company():
    """Company link is set when lead has no company yet."""
    service = _service()
    lead_payload = _lead_payload()

    service._ensure_lead_linked_to_company(lead_payload, company_id=COMPANY_ID, label="Buyer")

    assert lead_payload.company is not None
    assert lead_payload.company.company_id == COMPANY_ID
    assert lead_payload.company.label == "Buyer"


def test_build_create_response_data():
    """Response ids include lead, contact, and company identifiers."""
    result = ExternalLeadCreateResult(
        created={"id": LEAD_ID},
        lead_payload=_lead_payload(),
        created_contact_id=CONTACT_ID,
        created_company_id=COMPANY_ID,
        lead_company_id="lead-company-1",
        contact_created_events=[],
        lead_created_event=None,
        lead_event_key=None,
        contact_result=None,
        company_result=None,
    )

    data = ExternalLeadsService.build_create_response_data(result)

    assert data == {
        "lead_id": LEAD_ID,
        "contact_id": CONTACT_ID,
        "company_id": "lead-company-1",
        "contact_company_id": COMPANY_ID,
    }


def test_apply_create_audit_state():
    """Audit state captures lead and created entity metadata."""
    request = SimpleNamespace(state=SimpleNamespace())
    result = ExternalLeadCreateResult(
        created={"id": LEAD_ID, "name": "Enterprise Deal"},
        lead_payload=_lead_payload(),
        created_contact_id=CONTACT_ID,
        created_company_id=COMPANY_ID,
        lead_company_id=COMPANY_ID,
        contact_created_events=[],
        lead_created_event=None,
        lead_event_key=None,
        contact_result=None,
        company_result=None,
    )

    with patch(
        "apps.user_service.app.services.external_leads_service.LeadService._normalize_lead_audit_snapshot",
        return_value={"id": LEAD_ID},
    ):
        ExternalLeadsService.apply_create_audit_state(
            request,
            result=result,
            user_context=_ctx(),
            external_actor=True,
        )

    assert request.state.audit_table == "leads"
    assert request.state.audit_requested_id == LEAD_ID
    assert "new contact" in request.state.audit_description
    assert request.state.audit_risk_level == "high"
    assert request.state.raw_audit_new_data["created_contact_id"] == CONTACT_ID


def test_collect_created_entities_and_enrichment_targets():
    """Helpers merge created entities and enrichment targets from nested results."""
    result = ExternalLeadCreateResult(
        created={"id": LEAD_ID},
        lead_payload=_lead_payload(),
        created_contact_id=CONTACT_ID,
        created_company_id=COMPANY_ID,
        lead_company_id=COMPANY_ID,
        contact_created_events=[],
        lead_created_event=None,
        lead_event_key=None,
        contact_result={
            "created_entities": [{"entity_table": "contacts", "entity_id": CONTACT_ID}],
            "enrichment_targets": [{"entity_id": CONTACT_ID}],
        },
        company_result={
            "created_entities": [{"entity_table": "companies", "entity_id": COMPANY_ID}],
            "enrichment_targets": [{"entity_id": COMPANY_ID}],
        },
    )

    assert ExternalLeadsService._collect_created_entities(result) == [
        {"entity_table": "contacts", "entity_id": CONTACT_ID},
        {"entity_table": "companies", "entity_id": COMPANY_ID},
    ]
    assert ExternalLeadsService._collect_enrichment_targets(result) == [
        {"entity_id": CONTACT_ID},
        {"entity_id": COMPANY_ID},
    ]


@pytest.mark.asyncio
async def test_create_or_reuse_contact_reuses_on_email_conflict():
    """Duplicate email conflict reuses existing contact and adds phones."""
    service = _service()
    service.contacts_service.create_contact = AsyncMock(
        side_effect=ConflictException(
            message_key="contacts.errors.email_already_exists",
            params={"client_id": CONTACT_ID},
        )
    )
    service.contacts_service.add_phones_to_contact_if_missing = AsyncMock()
    contact_payload = CreateContactRequestStandalone(email="dup@example.com", phones=[])

    result, contact_id, company_id = await service._create_or_reuse_contact(contact_payload)

    assert contact_id == CONTACT_ID
    assert company_id is None
    assert result["reused_existing"] is True


@pytest.mark.asyncio
async def test_create_lead_with_optional_contact():
    """Full create flow links inline contact and creates lead."""
    service = _service()
    service.contacts_service.create_contact = AsyncMock(
        return_value={
            "contact_id": CONTACT_ID,
            "company_id": COMPANY_ID,
            "created_entities": [{"entity_table": "contacts", "entity_id": CONTACT_ID}],
        }
    )
    service.lead_service.create_lead = AsyncMock(
        return_value={"id": LEAD_ID, "name": "Enterprise Deal"}
    )
    service.event_service.create_lifecycle_event = AsyncMock(return_value={"event_id": "evt-1"})

    with patch(
        "apps.user_service.app.services.external_leads_service.ContactsService.create_lifecycle_events_for_created_entities",
        new=AsyncMock(return_value=[({"event_id": "evt-contact"}, CONTACT_ID)]),
    ):
        result = await service.create_lead_with_optional_contact(
            lead=_lead_payload(),
            contact=CreateContactRequestStandalone(email="buyer@example.com"),
            lead_contact_label="Primary",
            require_linked_contact=False,
            actor_user_id="user-1",
        )

    assert result.created_contact_id == CONTACT_ID
    assert result.created_company_id == COMPANY_ID
    assert result.lead_payload.contacts[0].contact_id == CONTACT_ID
    service.lead_service.create_lead.assert_awaited_once()


def test_schedule_create_post_commit_registers_background_tasks():
    """Post-commit scheduling publishes events and indexing tasks."""
    background_tasks = BackgroundTasks()
    result = ExternalLeadCreateResult(
        created={"id": LEAD_ID},
        lead_payload=_lead_payload(),
        created_contact_id=CONTACT_ID,
        created_company_id=None,
        lead_company_id=None,
        contact_created_events=[({"event_id": "evt-contact"}, CONTACT_ID)],
        lead_created_event={"event_id": "evt-lead"},
        lead_event_key=LEAD_ID,
        contact_result={
            "created_entities": [],
            "enrichment_targets": [{"entity_id": CONTACT_ID}],
        },
        company_result=None,
    )

    with (
        patch(
            "apps.user_service.app.services.external_leads_service.ContactsService.schedule_lifecycle_event_publishes"
        ) as mock_publish,
        patch(
            "apps.user_service.app.services.external_leads_service.ContactsService.schedule_typesense_indexing_for_created_entities"
        ) as mock_index,
        patch(
            "apps.user_service.app.services.external_leads_service.ContactsService.schedule_enrichment"
        ) as mock_enrich,
    ):
        ExternalLeadsService.schedule_create_post_commit(
            background_tasks,
            result=result,
            organization_id=ORG_ID,
            lead_kafka_topics=[KafkaTopics.CRM_EVENTS],
        )

    mock_publish.assert_called_once()
    mock_index.assert_called_once()
    mock_enrich.assert_called_once()
    assert len(background_tasks.tasks) >= 2


@pytest.mark.asyncio
async def test_create_or_reuse_contact_reraises_unrelated_conflict():
    """Only email-already-exists conflicts are converted to reuse."""
    service = _service()
    service.contacts_service.create_contact = AsyncMock(
        side_effect=ConflictException(message_key="contacts.errors.other")
    )
    with pytest.raises(ConflictException):
        await service._create_or_reuse_contact(
            CreateContactRequestStandalone(email="x@example.com", phones=[])
        )


@pytest.mark.asyncio
async def test_create_or_reuse_contact_conflict_without_client_id():
    """Email conflict without client_id cannot be reused."""
    service = _service()
    service.contacts_service.create_contact = AsyncMock(
        side_effect=ConflictException(
            message_key="contacts.errors.email_already_exists",
            params={},
        )
    )
    with pytest.raises(ConflictException):
        await service._create_or_reuse_contact(
            CreateContactRequestStandalone(email="dup@example.com", phones=[])
        )


def test_ensure_lead_linked_to_company_noop_on_empty_id():
    """Blank company ids are ignored."""
    service = _service()
    lead_payload = _lead_payload()
    service._ensure_lead_linked_to_company(lead_payload, company_id="  ")
    assert lead_payload.company is None


def test_ensure_lead_linked_to_company_keeps_existing_different_company():
    """Existing company link is not overwritten by a different id."""
    service = _service()
    lead_payload = _lead_payload(
        company=CreateLeadCompany(company_id="existing-co", label="Primary")
    )
    service._ensure_lead_linked_to_company(lead_payload, company_id=COMPANY_ID)
    assert lead_payload.company.company_id == "existing-co"


def test_apply_create_audit_state_company_only_medium_risk():
    """Lead-only company create uses medium risk when no inline contact."""
    request = SimpleNamespace(state=SimpleNamespace())
    result = ExternalLeadCreateResult(
        created={"id": LEAD_ID},
        lead_payload=_lead_payload(),
        created_contact_id=None,
        created_company_id=None,
        lead_company_id=COMPANY_ID,
        contact_created_events=[],
        lead_created_event=None,
        lead_event_key=None,
        contact_result=None,
        company_result=None,
    )
    with patch(
        "apps.user_service.app.services.external_leads_service.LeadService._normalize_lead_audit_snapshot",
        return_value={"id": LEAD_ID},
    ):
        ExternalLeadsService.apply_create_audit_state(
            request, result=result, user_context=_ctx(), external_actor=False
        )
    assert "new company" in request.state.audit_description
    assert request.state.audit_risk_level == "high"


def test_build_create_response_data_lead_company_only():
    """Response prefers lead_company_id over inline contact company."""
    result = ExternalLeadCreateResult(
        created={"id": LEAD_ID},
        lead_payload=_lead_payload(),
        created_contact_id=None,
        created_company_id=COMPANY_ID,
        lead_company_id="lead-co",
        contact_created_events=[],
        lead_created_event=None,
        lead_event_key=None,
        contact_result=None,
        company_result=None,
    )
    data = ExternalLeadsService.build_create_response_data(result)
    assert data["company_id"] == "lead-co"
    assert data["contact_company_id"] == COMPANY_ID


@pytest.mark.asyncio
async def test_create_lead_with_lead_only_company():
    """Inline company create links lead.company without contact."""
    service = _service()
    service.lead_service.create_lead = AsyncMock(return_value={"id": LEAD_ID})
    service.companies_service.create_company = AsyncMock(
        return_value={
            "company_id": COMPANY_ID,
            "created_entities": [{"entity_table": "companies", "entity_id": COMPANY_ID}],
        }
    )
    service.event_service.create_lifecycle_event = AsyncMock(return_value={"event_id": "evt-1"})
    with patch(
        "apps.user_service.app.services.external_leads_service.CompaniesService.create_lifecycle_events_for_created_entities",
        new=AsyncMock(return_value=[({"event_id": "evt-co"}, COMPANY_ID)]),
    ):
        result = await service.create_lead_with_optional_contact(
            lead=_lead_payload(contacts=[LeadContactCreate(contact_id=CONTACT_ID)]),
            contact=None,
            lead_contact_label=None,
            create_company=CreateCompanyRequestStandalone(name="Acme"),
            require_linked_contact=False,
        )
    assert result.lead_company_id == COMPANY_ID
    assert result.lead_payload.company.company_id == COMPANY_ID
