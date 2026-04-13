"""Unit tests for leads schemas."""

from __future__ import annotations

from datetime import date

import pytest

from apps.user_service.app.schemas.enums import DealType
from apps.user_service.app.schemas.lead_stages import UNSET
from apps.user_service.app.schemas.leads import (
    CreateLeadCompany,
    CreateLeadRequest,
    LeadCompaniesUpdate,
    LeadContactsUpdate,
    UpdateLeadRequest,
)
from libs.shared_utils.http_exceptions import ValidationException

CLIENT_ID = "11111111-1111-1111-1111-111111111111"
STAGE_ID = "22222222-2222-2222-2222-222222222222"
OWNER_ID = "33333333-3333-3333-3333-333333333333"


def test_create_lead_blank_optional_to_none():
    """CreateLeadRequest strips whitespace and converts blank strings to None."""
    req = CreateLeadRequest(
        name="Lead",
        stage_id=STAGE_ID,
        deal_type=DealType.NEW_BUSINESS,
        lead_source="   ",
        referral_source=" Partner  ",
        lead_score="   ",
        description="   ",
        owner_id=OWNER_ID,
        company=CreateLeadCompany(company_id=CLIENT_ID),
        close_date=date(2026, 1, 1),
    )

    assert req.lead_source is None
    assert req.referral_source == "Partner"
    assert req.lead_score is None
    assert req.description is None
    assert req.notes == []


def test_create_lead_deal_type_optional_and_nullable():
    """CreateLeadRequest allows omitted deal_type and JSON null."""
    omitted = CreateLeadRequest(name="Lead", stage_id=STAGE_ID)
    assert omitted.deal_type is None

    from_null = CreateLeadRequest.model_validate(
        {"name": "Lead", "stage_id": STAGE_ID, "deal_type": None}
    )
    assert from_null.deal_type is None


def test_update_lead_rejects_empty_payload():
    """UpdateLeadRequest rejects when no field is explicitly set."""
    with pytest.raises(ValidationException) as exc_info:
        UpdateLeadRequest()
    assert exc_info.value.message_key == "leads.errors.empty_update_payload"


def test_update_lead_normalizes_blank_strings_to_none():
    """UpdateLeadRequest strips and converts blank strings to None (not UNSET)."""
    req = UpdateLeadRequest(name="   ")
    assert req.name is None


def test_update_lead_stage_id_is_plain_string():
    """UpdateLeadRequest keeps ``stage_id`` as a string (UUID format enforced in services/API)."""
    req = UpdateLeadRequest(stage_id="not-a-uuid")
    assert req.stage_id == "not-a-uuid"


def test_update_lead_unset_no_changes():
    """UpdateLeadRequest treats UNSET as no-op when explicitly set."""
    req = UpdateLeadRequest(stage_id=UNSET)
    assert isinstance(req.stage_id, UNSET.__class__)


def test_update_lead_allows_contacts_update_only():
    req = UpdateLeadRequest(contacts_update=LeadContactsUpdate(remove_associations=[CLIENT_ID]))
    assert req.contacts_update is not None


def test_update_lead_allows_companies_update_only():
    req = UpdateLeadRequest(companies_update=LeadCompaniesUpdate(remove_associations=[CLIENT_ID]))
    assert req.companies_update is not None
