"""Unit tests for fee invoice payment logic."""

from __future__ import annotations

from typing import Any

import pytest

from apps.user_service.app.services.fee_invoice_service import FeeInvoiceService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ValidationException


def _user_context() -> UserContext:
    """Build a minimal user context for service tests."""
    return UserContext(user_id="user-1", email="admin@example.com", organization_id="org-1")


class _FakeInvoicesRepo:
    """In-memory fake MaintenanceFeeInvoicesRepository."""

    def __init__(self, row: dict[str, Any]):
        self.row = row
        self.last_patch: dict[str, Any] | None = None

    async def get_by_id(self, **_kwargs):
        """Return configured invoice row."""
        return self.row

    async def update_invoice(self, **_kwargs):
        """Apply patch to the fake invoice row."""
        self.last_patch = _kwargs.get("patch")
        updated = dict(self.row)
        updated.update(_kwargs.get("patch") or {})
        return updated


class _FakeEventsRepo:
    """In-memory fake MaintenanceFeeInvoiceEventsRepository."""

    def __init__(self):
        self.events: list[dict[str, Any]] = []

    async def insert(self, *, data):
        """Record timeline event."""
        self.events.append(data)
        return data


def _service(invoice: dict[str, Any]) -> FeeInvoiceService:
    """Build a FeeInvoiceService with fake repositories."""
    service = FeeInvoiceService.__new__(FeeInvoiceService)
    service._org_id = "org-1"
    service._user_id = "user-1"
    service.invoices_repo = _FakeInvoicesRepo(invoice)
    service.events_repo = _FakeEventsRepo()
    return service


@pytest.mark.asyncio
async def test_record_payment_marks_invoice_paid() -> None:
    """Full payment should mark the invoice as paid."""
    invoice = {
        "id": "inv-1",
        "amount_minor": 10000,
        "amount_paid_minor": 0,
        "currency": "INR",
        "status": "issued",
    }
    service = _service(invoice)
    result = await service.record_payment(invoice_id="inv-1")
    assert result["status"] == "paid"
    assert service.invoices_repo.last_patch["amount_paid_minor"] == 10000


@pytest.mark.asyncio
async def test_record_payment_rejects_overpayment() -> None:
    """Payments above the outstanding balance should be rejected."""
    invoice = {
        "id": "inv-1",
        "amount_minor": 10000,
        "amount_paid_minor": 0,
        "currency": "INR",
        "status": "issued",
    }
    service = _service(invoice)
    with pytest.raises(ValidationException):
        await service.record_payment(invoice_id="inv-1", amount_minor=20000)
