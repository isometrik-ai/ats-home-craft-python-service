"""Unit tests for leads schemas."""

from __future__ import annotations

from datetime import date

import pytest

from apps.user_service.app.schemas.enums import DealType, LeadCurrency
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


def test_lead_requires_currency_when_amount_present():
    """CreateLeadRequest requires currency when amount is provided."""
    with pytest.raises(ValidationException):
        CreateLeadRequest(name="Lead", stage_id=STAGE_ID, amount="100.00")

    ok = CreateLeadRequest(
        name="Lead",
        stage_id=STAGE_ID,
        amount="100.00",
        currency=LeadCurrency.USD,
    )
    assert ok.currency == LeadCurrency.USD


def test_lead_requires_currency_when_amount():
    """UpdateLeadRequest requires currency when amount is provided (non-null)."""
    with pytest.raises(ValidationException):
        UpdateLeadRequest(amount="100.00")

    ok = UpdateLeadRequest(amount="100.00", currency=LeadCurrency.EUR)
    assert ok.amount is not None

    cleared = UpdateLeadRequest(amount=None)
    assert cleared.amount is None


def test_currency_rejected_when_amount_missing_or_null():
    """currency must not be sent unless amount is provided and non-null."""
    with pytest.raises(ValidationException):
        CreateLeadRequest(name="Lead", stage_id=STAGE_ID, currency=LeadCurrency.USD)

    with pytest.raises(ValidationException):
        UpdateLeadRequest(currency=LeadCurrency.USD)

    with pytest.raises(ValidationException):
        UpdateLeadRequest(amount=None, currency=LeadCurrency.USD)


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
    """UpdateLeadRequest accepts contacts_update without requiring other fields."""
    req = UpdateLeadRequest(contacts_update=LeadContactsUpdate(remove_associations=[CLIENT_ID]))
    assert req.contacts_update is not None


def test_update_lead_allows_companies_update_only():
    """UpdateLeadRequest accepts companies_update without requiring other fields."""
    req = UpdateLeadRequest(companies_update=LeadCompaniesUpdate(remove_associations=[CLIENT_ID]))
    assert req.companies_update is not None


def test_lead_contact_create_normalizes_blank_label():
    """LeadContactCreate strips label and converts blanks to None."""
    from apps.user_service.app.schemas.leads import LeadContactCreate

    item = LeadContactCreate(contact_id=CLIENT_ID, label="   ")
    assert item.label is None

    item2 = LeadContactCreate(contact_id=CLIENT_ID, label=" primary ")
    assert item2.label == "primary"


def test_lead_contacts_update_requires_operation():
    """LeadContactsUpdate rejects empty delta payloads."""
    with pytest.raises(ValueError, match="at least one operation"):
        LeadContactsUpdate()


def test_lead_contacts_update_normalizes_add_associations():
    """LeadContactsUpdate trims contact ids on add."""
    payload = LeadContactsUpdate(
        add_associations=[{"contact_id": f"  {CLIENT_ID}  ", "label": " primary "}]
    )
    assert payload.add_associations[0].contact_id == CLIENT_ID
    assert payload.add_associations[0].label == "primary"


def test_lead_contacts_update_rejects_missing_add_contact_id():
    """add_associations entries require contact_id."""
    with pytest.raises(ValueError, match="add_associations.contact_id"):
        LeadContactsUpdate(add_associations=[{"contact_id": "   ", "label": "x"}])


def test_lead_companies_update_requires_operation():
    """LeadCompaniesUpdate rejects empty delta payloads."""
    with pytest.raises(ValueError, match="at least one operation"):
        LeadCompaniesUpdate()


def test_create_lead_rejects_company_link_and_inline_create():
    """company.company_id and create_company are mutually exclusive."""
    from apps.user_service.app.schemas.companies import CreateCompanyRequestStandalone

    with pytest.raises(ValidationException):
        CreateLeadRequest(
            name="Lead",
            stage_id=STAGE_ID,
            company=CreateLeadCompany(company_id=CLIENT_ID),
            create_company=CreateCompanyRequestStandalone(name="Acme"),
        )


def test_leads_list_query_invalid_date_range():
    """LeadsListQueryParams rejects start_date after end_date."""
    from apps.user_service.app.schemas.enums import LeadsListMode
    from apps.user_service.app.schemas.leads import LeadsListQueryParams

    with pytest.raises(ValidationException):
        LeadsListQueryParams(
            mode=LeadsListMode.LIST,
            start_date=date(2026, 2, 1),
            end_date=date(2026, 1, 1),
        )


def test_update_lead_rejects_null_contacts_update_object():
    """Explicit null contacts_update is rejected."""
    with pytest.raises(ValueError, match="contacts_update must be an object"):
        UpdateLeadRequest.model_validate({"name": "X", "contacts_update": None})


def test_update_lead_rejects_null_companies_update_object():
    """Explicit null companies_update is rejected."""
    with pytest.raises(ValueError, match="companies_update must be an object"):
        UpdateLeadRequest.model_validate({"name": "X", "companies_update": None})


def test_lead_contacts_update_rejects_missing_update_contact_id():
    """update_associations entries require contact_id."""
    with pytest.raises(ValueError, match="update_associations.contact_id"):
        LeadContactsUpdate(update_associations=[{"contact_id": "  ", "label": "x"}])


def test_lead_companies_update_normalizes_remove_ids():
    """LeadCompaniesUpdate trims company ids on remove."""
    payload = LeadCompaniesUpdate(remove_associations=[f"  {CLIENT_ID}  ", ""])
    assert payload.remove_associations == [CLIENT_ID]


def test_leads_list_search_blank_becomes_none():
    """LeadsListQueryParams strips blank search."""
    from apps.user_service.app.schemas.enums import LeadsListMode
    from apps.user_service.app.schemas.leads import LeadsListQueryParams

    params = LeadsListQueryParams(mode=LeadsListMode.LIST, search="   ")
    assert params.search is None


def test_create_lead_company_label_blank():
    """CreateLeadCompany strips blank labels."""
    item = CreateLeadCompany(company_id=CLIENT_ID, label="   ")
    assert item.label is None


def test_lead_validators_non_string_label_passthrough():
    """Label validators return non-string values unchanged."""
    from apps.user_service.app.schemas.leads import (
        LeadCompanyAssociationUpdate,
        LeadCompanyCreate,
        LeadContactAssociationUpdate,
        LeadContactCreate,
    )

    assert LeadContactCreate.normalize_label(123) == 123
    assert LeadCompanyCreate.normalize_label(456) == 456
    assert LeadContactAssociationUpdate.normalize_label(789) == 789
    assert LeadCompanyAssociationUpdate.normalize_label(101) == 101
    assert LeadContactAssociationUpdate.normalize_contact_id(999) == 999
    assert LeadCompanyAssociationUpdate.normalize_company_id(888) == 888


def test_update_lead_normalize_blank_strings_branches():
    """UpdateLeadRequest blank-string normalizer preserves UNSET and passthrough values."""
    from apps.user_service.app.schemas.lead_stages import UNSET

    assert UpdateLeadRequest.normalize_blank_strings(UNSET) is UNSET
    assert UpdateLeadRequest.normalize_blank_strings(None) is None
    assert UpdateLeadRequest.normalize_blank_strings(99) == 99
    assert UpdateLeadRequest.normalize_blank_strings("  hi  ") == "hi"
