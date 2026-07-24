"""Integration tests for fee invoice admin endpoints."""

import pytest

from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.tests.utils.assertions import assert_success

PROJECT_ID = "proj-1"
INVOICE_ID = "inv-1"

_FAKE_INVOICE = {
    "id": INVOICE_ID,
    "project_id": PROJECT_ID,
    "unit_id": "unit-1",
    "unit_code": "A-101",
    "unit_label": "Apartment 101",
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
    "metadata": {},
    "issued_at": "2026-07-01T00:00:00Z",
    "paid_at": None,
    "created_at": "2026-07-01T00:00:00Z",
    "updated_at": "2026-07-01T00:00:00Z",
}

_FAKE_GENERATE_RESULT = {
    "created_count": 2,
    "skipped_count": 1,
    "invoice_ids": ["inv-1", "inv-2"],
}

_FAKE_SCHEDULER_RESULT = {
    "projects_processed": 1,
    "generation": [
        {"project_id": PROJECT_ID, "created_count": 2, "skipped_count": 0, "invoice_ids": ["inv-1"]}
    ],
    "reminders": {"processed_count": 3},
    "retries": {"processed_count": 1, "escalated_count": 0},
}


def _ctx() -> UserContext:
    """Return a reusable admin user context."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id="org-1",
        user_type="admin",
    )


def _patch_check_permissions(monkeypatch, module_path: str) -> None:
    """Patch check_permissions to return a fake admin context."""

    async def fake_check_permissions(
        current_user, db_connection, permission_codes, organization_id=None
    ):
        del current_user, db_connection, permission_codes, organization_id
        return _ctx()

    monkeypatch.setattr(f"{module_path}.check_permissions", fake_check_permissions)


@pytest.mark.asyncio
async def test_list_fee_invoices(monkeypatch, client):
    """GET fee invoices returns paginated project invoice list."""

    _patch_check_permissions(monkeypatch, "apps.user_service.app.api.fee_invoices")

    async def fake_list_project_invoices(
        _self, *, project_id: str, status=None, page=1, page_size=50
    ):
        del _self
        assert project_id == PROJECT_ID
        assert status == "pending"
        assert page == 1
        assert page_size == 10
        return {"items": [_FAKE_INVOICE], "total": 1, "page": page, "page_size": page_size}

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_invoice_service."
        "FeeInvoiceService.list_project_invoices",
        fake_list_project_invoices,
    )

    res = await client.get(
        f"/v1/projects/{PROJECT_ID}/fee-invoices",
        params={"status": "pending", "page": 1, "page_size": 10},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["id"] == INVOICE_ID
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_fee_invoice(monkeypatch, client):
    """GET fee invoice by id returns invoice detail."""

    _patch_check_permissions(monkeypatch, "apps.user_service.app.api.fee_invoices")

    async def fake_get_invoice(_self, *, invoice_id: str, project_id=None):
        del _self
        assert invoice_id == INVOICE_ID
        assert project_id == PROJECT_ID
        return _FAKE_INVOICE

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_invoice_service.FeeInvoiceService.get_invoice",
        fake_get_invoice,
    )

    res = await client.get(f"/v1/projects/{PROJECT_ID}/fee-invoices/{INVOICE_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == INVOICE_ID
    assert body["data"]["amount"] == 5000.0


@pytest.mark.asyncio
async def test_generate_fee_invoices(monkeypatch, client):
    """POST generate creates invoices for billable units."""

    _patch_check_permissions(monkeypatch, "apps.user_service.app.api.fee_invoices")

    async def fake_generate_invoices_for_project(_self, *, project_id: str):
        del _self
        assert project_id == PROJECT_ID
        return _FAKE_GENERATE_RESULT

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_invoice_service."
        "FeeInvoiceService.generate_invoices_for_project",
        fake_generate_invoices_for_project,
    )

    res = await client.post(f"/v1/projects/{PROJECT_ID}/fee-invoices/generate")
    body = assert_success(res, 200)
    assert body["data"]["created_count"] == 2
    assert body["data"]["invoice_ids"] == ["inv-1", "inv-2"]


@pytest.mark.asyncio
async def test_run_fee_billing_scheduler(monkeypatch, client):
    """POST fee-billing/run executes the billing scheduler."""

    _patch_check_permissions(monkeypatch, "apps.user_service.app.api.fee_invoices")

    async def fake_run_billing_cycle(_self):
        del _self
        return _FAKE_SCHEDULER_RESULT

    monkeypatch.setattr(
        "apps.user_service.app.services.fee_scheduler_service."
        "FeeSchedulerService.run_billing_cycle",
        fake_run_billing_cycle,
    )

    res = await client.post("/v1/projects/fee-billing/run")
    body = assert_success(res, 200)
    assert body["data"]["projects_processed"] == 1
    assert body["data"]["reminders"]["processed_count"] == 3
