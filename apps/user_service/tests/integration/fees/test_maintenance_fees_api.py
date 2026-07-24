"""Integration tests for resident maintenance fee endpoints."""

import pytest

from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success

CONTACT_UNIT_ID = "cu-1"
INVOICE_ID = "inv-1"

_FAKE_INVOICE = {
    "id": INVOICE_ID,
    "project_id": "proj-1",
    "unit_id": "unit-1",
    "unit_code": "A-101",
    "unit_label": "Apartment 101",
    "contact_unit_id": CONTACT_UNIT_ID,
    "period_start": "2026-07-01",
    "period_end": "2026-07-31",
    "due_date": "2026-07-15",
    "amount": 5000.0,
    "amount_paid": 0.0,
    "outstanding_amount": 5000.0,
    "currency": "INR",
    "status": "pending",
    "retry_attempts": 0,
    "reminders_sent": 0,
    "escalated_at": None,
}

_PAID_INVOICE = {
    **_FAKE_INVOICE,
    "amount_paid": 5000.0,
    "outstanding_amount": 0.0,
    "status": "paid",
    "paid_at": "2026-07-10T00:00:00Z",
}


def _ctx() -> UserContext:
    """Return a reusable resident user context."""
    return UserContext(
        user_id="u1",
        email="resident@example.com",
        organization_id="org-1",
        user_type="contact",
    )


def _patch_contact_context(monkeypatch) -> None:
    """Patch onboarding contact context for maintenance fee routes."""

    async def fake_extract_onboarding_contact_context(current_user, db_connection, request=None):
        del current_user, db_connection, request
        return _ctx(), {"id": "contact-1"}

    monkeypatch.setattr(
        "apps.user_service.app.api.maintenance_fees.extract_onboarding_contact_context",
        fake_extract_onboarding_contact_context,
    )


def _patch_contact_units(monkeypatch) -> None:
    """Patch contact unit ownership lookup."""

    async def fake_list_by_contact(_self, *, organization_id: str, contact_id: str):
        del _self
        assert organization_id == "org-1"
        assert contact_id == "contact-1"
        return [{"id": CONTACT_UNIT_ID, "status": "active"}]

    monkeypatch.setattr(
        "apps.user_service.app.db.repositories.contact_units_repository."
        "ContactUnitsRepository.list_by_contact",
        fake_list_by_contact,
    )


@pytest.mark.asyncio
async def test_list_my_fee_invoices(monkeypatch, client):
    """GET maintenance-fees/invoices lists resident-owned invoices."""

    _patch_contact_context(monkeypatch)
    _patch_contact_units(monkeypatch)

    async def fake_list_resident_invoices(_self, *, contact_unit_ids, page=1, page_size=50):
        del _self
        assert contact_unit_ids == [CONTACT_UNIT_ID]
        assert page == 1
        assert page_size == 20
        return {
            "items": [_FAKE_INVOICE],
            "total": 1,
            "page": page,
            "page_size": page_size,
        }

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_invoice_service."
        "FeeInvoiceService.list_resident_invoices",
        fake_list_resident_invoices,
    )

    res = await client.get(
        "/v1/maintenance-fees/invoices",
        params={"page": 1, "page_size": 20},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == INVOICE_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_pay_fee_invoice(monkeypatch, client):
    """POST pay records payment for an owned invoice."""

    _patch_contact_context(monkeypatch)
    _patch_contact_units(monkeypatch)

    async def fake_get_invoice(_self, *, invoice_id: str, project_id=None):
        del _self, project_id
        assert invoice_id == INVOICE_ID
        return _FAKE_INVOICE

    async def fake_record_payment(_self, *, invoice_id: str, amount_minor=None, actor_user_id=None):
        del _self, amount_minor
        assert invoice_id == INVOICE_ID
        assert actor_user_id == "u1"
        return _PAID_INVOICE

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_invoice_service.FeeInvoiceService.get_invoice",
        fake_get_invoice,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.fee_invoice_service.FeeInvoiceService.record_payment",
        fake_record_payment,
    )

    res = await client.post(
        f"/v1/maintenance-fees/invoices/{INVOICE_ID}/pay",
        json={"amount": 5000.0},
    )
    body = assert_success(res, 200)
    assert body["data"]["status"] == "paid"
    assert body["data"]["outstanding_amount"] == 0.0
