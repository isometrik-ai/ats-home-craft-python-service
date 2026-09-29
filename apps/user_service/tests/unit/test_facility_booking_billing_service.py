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
    WalletLimitRequest,
    WalletTopUpRequest,
)
from apps.user_service.app.services.facility_booking_billing_service import (
    FacilityBookingBillingService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)

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


@pytest.mark.asyncio
async def test_get_wallet_and_set_limit():
    svc = _service()
    result = await svc.get_wallet(project_id=PROJECT_ID, contact_id=CONTACT_ID)
    assert result["balance"] == 500
    assert result["credit_limit"] == 10000

    svc.wallets.set_limit = AsyncMock()
    await svc.set_limit(
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        body=WalletLimitRequest(limit=2000),
    )
    svc.wallets.set_limit.assert_awaited_once()


@pytest.mark.asyncio
async def test_top_up_success_and_disabled_cash():
    svc = _service()
    svc.config_service.get_settings = AsyncMock(
        return_value={
            "invoice_frequency": "monthly",
            "wallet_enabled": True,
            "online_enabled": True,
            "cash_enabled": False,
            "wallet_credit_limit": 10000,
        }
    )
    with pytest.raises(ValidationException):
        await svc.top_up(
            project_id=PROJECT_ID,
            body=WalletTopUpRequest(
                contact_id=CONTACT_ID, amount=100, method=FacilityBookingPaymentMethod.CASH
            ),
        )

    svc.config_service.get_settings = AsyncMock(
        return_value={
            "invoice_frequency": "monthly",
            "wallet_enabled": True,
            "online_enabled": True,
            "cash_enabled": True,
            "wallet_credit_limit": 10000,
        }
    )
    result = await svc.top_up(
        project_id=PROJECT_ID,
        body=WalletTopUpRequest(
            contact_id=CONTACT_ID, amount=100, method=FacilityBookingPaymentMethod.CASH
        ),
    )
    assert result["balance"] == 800
    svc.wallets.insert_transaction.assert_awaited()


@pytest.mark.asyncio
async def test_adjust_success_and_zero_amount():
    svc = _service()
    with pytest.raises(ValidationException):
        await svc.adjust(
            project_id=PROJECT_ID,
            body=WalletAdjustRequest(contact_id=CONTACT_ID, amount=0, reason="noop"),
        )

    await svc.adjust(
        project_id=PROJECT_ID,
        body=WalletAdjustRequest(contact_id=CONTACT_ID, amount=100, reason="Bonus"),
    )
    svc.notifier.wallet_updated.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_and_get_invoice():
    svc = _service()
    svc.invoices.list_project = AsyncMock(
        return_value=(
            [
                {
                    "id": INVOICE_ID,
                    "contact_id": CONTACT_ID,
                    "number": "INV-1",
                    "status": "issued",
                    "lines": [],
                    "total": 100,
                    "period_label": "Sep",
                    "due_date": date(2026, 10, 1),
                }
            ],
            1,
        )
    )
    rows, total = await svc.list_invoices(project_id=PROJECT_ID)
    assert total == 1
    assert rows[0]["number"] == "INV-1"

    svc.invoices.get = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.get_invoice(project_id=PROJECT_ID, invoice_id=INVOICE_ID)

    svc.invoices.get = AsyncMock(
        return_value={
            "id": INVOICE_ID,
            "contact_id": "other",
            "number": "INV-1",
            "status": "issued",
            "lines": [],
            "total": 1,
            "period_label": "Sep",
            "due_date": date(2026, 10, 1),
        }
    )
    with pytest.raises(ForbiddenException):
        await svc.get_invoice(project_id=PROJECT_ID, invoice_id=INVOICE_ID, contact_id=CONTACT_ID)


@pytest.mark.asyncio
async def test_generate_invoices_skips_non_positive_totals():
    svc = _service()
    svc.invoices.count_for_year = AsyncMock(return_value=0)
    svc.ledger_repo.list_invoiceable = AsyncMock(
        return_value=[
            {
                "id": "e1",
                "contact_id": CONTACT_ID,
                "description": "Refund",
                "amount": -100,
                "reservation_id": None,
            }
        ]
    )
    result = await svc.generate_invoices(project_id=PROJECT_ID, body=GenerateInvoicesRequest())
    assert result["created"] == 0
    svc.invoices.insert.assert_not_called()


@pytest.mark.asyncio
async def test_pay_invoice_cash_and_wallet_limit():
    svc = _service()
    svc.invoices.get = AsyncMock(
        return_value={
            "id": INVOICE_ID,
            "contact_id": CONTACT_ID,
            "number": "INV-1",
            "status": "issued",
            "lines": [],
            "total": 50000,
            "period_label": "Sep",
            "due_date": date(2026, 10, 1),
        }
    )
    with pytest.raises(ValidationException):
        await svc.pay_invoice(
            project_id=PROJECT_ID,
            invoice_id=INVOICE_ID,
            body=PayInvoiceRequest(method=FacilityBookingPaymentMethod.WALLET),
        )

    svc.invoices.get = AsyncMock(
        return_value={
            "id": INVOICE_ID,
            "contact_id": CONTACT_ID,
            "number": "INV-1",
            "status": "issued",
            "lines": [],
            "total": 200,
            "period_label": "Sep",
            "due_date": date(2026, 10, 1),
        }
    )
    svc.invoices.mark_paid = AsyncMock(
        return_value={
            "id": INVOICE_ID,
            "contact_id": CONTACT_ID,
            "number": "INV-1",
            "status": "paid",
            "lines": [],
            "total": 200,
            "period_label": "Sep",
            "due_date": date(2026, 10, 1),
            "paid_via": "cash",
        }
    )
    paid = await svc.pay_invoice(
        project_id=PROJECT_ID,
        invoice_id=INVOICE_ID,
        body=PayInvoiceRequest(method=FacilityBookingPaymentMethod.CASH),
    )
    assert paid["status"] == "paid"
    svc.wallets.update_balance.assert_not_called()


@pytest.mark.asyncio
async def test_overview_aggregates_metrics():
    svc = _service()
    svc.ledger = MagicMock()
    svc.ledger.unbilled = AsyncMock(return_value={"unbilled_total": 300, "unbilled_contacts": 2})
    svc.invoices.list_project = AsyncMock(
        return_value=(
            [
                {
                    "id": INVOICE_ID,
                    "contact_id": CONTACT_ID,
                    "number": "INV-1",
                    "status": "issued",
                    "lines": [],
                    "total": 200,
                    "period_label": "Sep",
                    "due_date": date(2026, 10, 1),
                }
            ],
            1,
        )
    )
    svc.ledger_repo.list_payments_for_month = AsyncMock(
        return_value=[{"method": "wallet", "amount": -50}]
    )
    svc.wallets.list_project = AsyncMock(return_value=[{"contact_id": CONTACT_ID, "balance": 400}])
    overview = await svc.overview(project_id=PROJECT_ID)
    assert overview["unbilled_total"] == 300
    assert overview["outstanding_count"] == 1
    assert overview["wallet_float"] == 400


@pytest.mark.asyncio
async def test_require_member_and_payment_guards():
    svc = _service()
    svc.contact_units_repo.contact_has_active_project_membership = AsyncMock(return_value=False)
    with pytest.raises(ValidationException):
        await svc.get_wallet(project_id=PROJECT_ID, contact_id=CONTACT_ID)

    svc.contact_units_repo.contact_has_active_project_membership = AsyncMock(return_value=True)
    svc.invoices.get = AsyncMock(
        return_value={
            "id": INVOICE_ID,
            "contact_id": CONTACT_ID,
            "number": "INV-1",
            "status": "issued",
            "lines": [],
            "total": 100,
            "period_label": "Sep",
            "due_date": date(2026, 10, 1),
        }
    )
    with pytest.raises(ValidationException):
        await svc.pay_invoice(
            project_id=PROJECT_ID,
            invoice_id=INVOICE_ID,
            body=PayInvoiceRequest(method=FacilityBookingPaymentMethod.ONLINE),
            allowed_methods={"cash"},
        )

    svc.config_service.get_settings = AsyncMock(
        return_value={
            "invoice_frequency": "monthly",
            "wallet_enabled": False,
            "online_enabled": False,
            "cash_enabled": False,
            "wallet_credit_limit": 10000,
        }
    )
    with pytest.raises(ValidationException):
        await svc.pay_invoice(
            project_id=PROJECT_ID,
            invoice_id=INVOICE_ID,
            body=PayInvoiceRequest(method=FacilityBookingPaymentMethod.CASH),
        )


@pytest.mark.asyncio
async def test_effective_limit_uses_wallet_override():
    svc = _service()
    svc.wallets.get_or_create = AsyncMock(
        return_value={
            "id": "w1",
            "contact_id": CONTACT_ID,
            "balance": 500,
            "credit_limit": 2500,
        }
    )
    wallet = await svc.get_wallet(project_id=PROJECT_ID, contact_id=CONTACT_ID)
    assert wallet["credit_limit"] == 2500
    assert wallet["custom_limit"] is True
