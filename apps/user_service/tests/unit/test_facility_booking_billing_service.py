"""Unit tests for FacilityBookingBillingService."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import FacilityBookingPaymentMethod
from apps.user_service.app.schemas.facility_booking import (
    GenerateInvoicesRequest,
    PayInvoiceRequest,
    WalletAdjustRequest,
    WalletTopUpRequest,
)
from apps.user_service.app.services.facility_booking_billing_service import (
    FacilityBookingBillingService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ValidationException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
CONTACT_ID = "33333333-3333-3333-3333-333333333333"
INVOICE_ID = "44444444-4444-4444-4444-444444444444"


def _service() -> FacilityBookingBillingService:
    svc = FacilityBookingBillingService(
        db_connection=MagicMock(),
        user_context=UserContext(user_id="user-1", email="a@b.com", organization_id="org-1"),
    )
    svc.wallets = MagicMock()
    svc.invoices = MagicMock()
    svc.ledger_repo = MagicMock()
    svc.ledger = MagicMock()
    svc.ledger.post = AsyncMock()
    svc.config_service = MagicMock()
    svc.config_service.get_settings = AsyncMock(
        return_value={
            "invoice_frequency": "monthly",
            "wallet_enabled": True,
            "online_enabled": True,
            "cash_enabled": True,
            "wallet_credit_limit": 10000,
        }
    )
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.contact_units_repo = MagicMock()
    svc.contact_units_repo.contact_has_active_project_membership = AsyncMock(return_value=True)
    svc.notifier = MagicMock()
    svc.notifier.invoice_generated = AsyncMock()
    svc.notifier.payment_received = AsyncMock()
    svc.notifier.wallet_updated = AsyncMock()
    svc.wallets.get_or_create = AsyncMock(
        return_value={"id": "w1", "contact_id": CONTACT_ID, "balance": 500, "credit_limit": None}
    )
    svc.wallets.list_transactions = AsyncMock(return_value=[])
    svc.wallets.update_balance = AsyncMock(return_value={"balance": 800})
    svc.wallets.insert_transaction = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_top_up_rejects_wallet_method():
    svc = _service()
    with pytest.raises(ValidationException):
        await svc.top_up(
            project_id=PROJECT_ID,
            body=WalletTopUpRequest(
                contact_id=CONTACT_ID, amount=100, method=FacilityBookingPaymentMethod.WALLET
            ),
        )


@pytest.mark.asyncio
async def test_adjust_blocks_credit_limit():
    svc = _service()
    with pytest.raises(ValidationException):
        await svc.adjust(
            project_id=PROJECT_ID,
            body=WalletAdjustRequest(contact_id=CONTACT_ID, amount=-20000, reason="Write-off"),
        )


@pytest.mark.asyncio
async def test_generate_invoices_groups_by_contact():
    svc = _service()
    svc.ledger_repo.list_invoiceable = AsyncMock(
        return_value=[
            {
                "id": "e1",
                "contact_id": CONTACT_ID,
                "description": "Tennis — booking",
                "amount": 400,
                "reservation_id": "r1",
            },
            {
                "id": "e2",
                "contact_id": CONTACT_ID,
                "description": "Tennis — deposit",
                "amount": 100,
                "reservation_id": "r1",
            },
        ]
    )
    svc.invoices.count_for_year = AsyncMock(return_value=2)
    svc.invoices.insert = AsyncMock(
        return_value={
            "id": INVOICE_ID,
            "contact_id": CONTACT_ID,
            "number": "INV-2026-0003",
            "status": "issued",
            "lines": [],
            "total": 500,
            "period_label": "September 2026",
            "due_date": date(2026, 10, 24),
            "created_at": datetime(2026, 9, 24, tzinfo=timezone.utc),
        }
    )
    svc.ledger_repo.attach_invoice = AsyncMock()

    result = await svc.generate_invoices(project_id=PROJECT_ID, body=GenerateInvoicesRequest())

    assert result["created"] == 1
    assert result["amount"] == 500
    inserted = svc.invoices.insert.await_args.args[0]
    assert inserted["number"] == "INV-2026-0003"
    assert inserted["total"] == 500
    svc.ledger_repo.attach_invoice.assert_awaited_once()
    svc.notifier.invoice_generated.assert_awaited_once()


@pytest.mark.asyncio
async def test_pay_invoice_wallet_posts_payment():
    svc = _service()
    svc.invoices.get = AsyncMock(
        return_value={
            "id": INVOICE_ID,
            "contact_id": CONTACT_ID,
            "number": "INV-2026-0003",
            "status": "issued",
            "lines": [],
            "total": 200,
            "period_label": "September 2026",
            "due_date": date(2026, 10, 24),
            "contact_name": "Ada",
        }
    )
    svc.invoices.mark_paid = AsyncMock(
        return_value={
            "id": INVOICE_ID,
            "contact_id": CONTACT_ID,
            "number": "INV-2026-0003",
            "status": "paid",
            "lines": [],
            "total": 200,
            "period_label": "September 2026",
            "due_date": date(2026, 10, 24),
            "paid_via": "wallet",
        }
    )

    result = await svc.pay_invoice(
        project_id=PROJECT_ID,
        invoice_id=INVOICE_ID,
        body=PayInvoiceRequest(method=FacilityBookingPaymentMethod.WALLET),
    )

    assert result["status"] == "paid"
    svc.wallets.update_balance.assert_awaited_once()
    posted = svc.ledger.post.await_args.kwargs
    assert posted["entry_type"] == "payment"
    assert posted["amount"] == -200
    svc.notifier.payment_received.assert_awaited_once()


@pytest.mark.asyncio
async def test_pay_invoice_rejects_already_paid():
    svc = _service()
    svc.invoices.get = AsyncMock(
        return_value={
            "id": INVOICE_ID,
            "contact_id": CONTACT_ID,
            "status": "paid",
            "total": 200,
        }
    )
    with pytest.raises(ValidationException):
        await svc.pay_invoice(
            project_id=PROJECT_ID,
            invoice_id=INVOICE_ID,
            body=PayInvoiceRequest(method=FacilityBookingPaymentMethod.CASH),
        )
