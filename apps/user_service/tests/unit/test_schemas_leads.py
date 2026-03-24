"""Unit tests for leads schemas."""

from __future__ import annotations

from datetime import date

import pytest

from apps.user_service.app.schemas.enums import LeadStatus
from apps.user_service.app.schemas.lead_stages import UNSET
from apps.user_service.app.schemas.leads import (
    CreateLeadRequest,
    UpdateLeadRequest,
)
from libs.shared_utils.http_exceptions import ValidationException

CLIENT_ID = "11111111-1111-1111-1111-111111111111"
STAGE_ID = "22222222-2222-2222-2222-222222222222"
OWNER_ID = "33333333-3333-3333-3333-333333333333"
POC_ID = "44444444-4444-4444-4444-444444444444"


def test_create_lead_blank_optional_to_none():
    """CreateLeadRequest strips whitespace and converts blank strings to None."""
    req = CreateLeadRequest(
        client_id=CLIENT_ID,
        name="Lead",
        stage_id=STAGE_ID,
        intake_stage="   ",
        lead_source="   ",
        referral_source=" Partner  ",
        lead_score="   ",
        notes="   ",
        description="   ",
        owner_id=OWNER_ID,
        point_of_contact=POC_ID,
        lead_status=LeadStatus.PROSPECT,
        close_date=date(2026, 1, 1),
    )

    assert req.intake_stage is None
    assert req.lead_source is None
    assert req.referral_source == "Partner"
    assert req.lead_score is None
    assert req.notes is None
    assert req.description is None


def test_create_lead_rejects_invalid_uuid_fields():
    """CreateLeadRequest validates UUID fields."""
    with pytest.raises(ValidationException) as exc_info:
        CreateLeadRequest(
            client_id="not-a-uuid",
            name="Lead",
            stage_id=STAGE_ID,
        )
    assert exc_info.value.message_key == "errors.invalid_uuid_format"


def test_update_lead_rejects_empty_payload():
    """UpdateLeadRequest rejects when no field is explicitly set."""
    with pytest.raises(ValidationException) as exc_info:
        UpdateLeadRequest()
    assert exc_info.value.message_key == "leads.errors.empty_update_payload"


def test_update_lead_normalizes_blank_strings_to_none():
    """UpdateLeadRequest strips and converts blank strings to None (not UNSET)."""
    req = UpdateLeadRequest(name="   ")
    assert req.name is None


def test_update_lead_uuid_validation():
    """UpdateLeadRequest validates UUIDs when field is present and not UNSET/None."""
    with pytest.raises(ValidationException) as exc_info:
        UpdateLeadRequest(stage_id="not-a-uuid")
    assert exc_info.value.message_key == "errors.invalid_uuid_format"


def test_update_lead_unset_no_changes():
    """UpdateLeadRequest treats UNSET as no-op when explicitly set."""
    req = UpdateLeadRequest(stage_id=UNSET)
    assert isinstance(req.stage_id, UNSET.__class__)
