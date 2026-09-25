"""Contact wallets, booking invoices and payment collection."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.facility_booking_invoice_repository import (
    FacilityBookingInvoiceRepository,
)
from apps.user_service.app.db.repositories.facility_booking_ledger_repository import (
    FacilityBookingLedgerRepository,
)
from apps.user_service.app.db.repositories.facility_booking_wallet_repository import (
    FacilityBookingWalletRepository,
)
from apps.user_service.app.schemas.enums import (
    INVOICE_FREQUENCY_DAYS,
    FacilityBookingInvoiceStatus,
    FacilityBookingLedgerType,
    FacilityBookingPaymentMethod,
    FacilityBookingWalletTxnType,
)
from apps.user_service.app.schemas.facility_booking import (
    BookingInvoiceResponse,
    GenerateInvoicesRequest,
    InvoiceLineResponse,
    PayInvoiceRequest,
    PaymentOverviewResponse,
    WalletAdjustRequest,
    WalletLimitRequest,
    WalletResponse,
    WalletTopUpRequest,
    WalletTransactionResponse,
)
from apps.user_service.app.services.facility_booking_config_service import (
    FacilityBookingConfigService,
)
from apps.user_service.app.services.facility_booking_ledger_service import (
    FacilityBookingLedgerService,
)
from apps.user_service.app.services.facility_booking_notification_service import (
    FacilityBookingNotificationService,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class FacilityBookingBillingService:
    """Wallet and invoice workflows for facility bookings."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.wallets = FacilityBookingWalletRepository(db_connection)
        self.invoices = FacilityBookingInvoiceRepository(db_connection)
        self.ledger_repo = FacilityBookingLedgerRepository(db_connection)
        self.ledger = FacilityBookingLedgerService(
            db_connection=db_connection, user_context=user_context
        )
        self.config_service = FacilityBookingConfigService(
            db_connection=db_connection, user_context=user_context
        )
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.notifier = FacilityBookingNotificationService(
            db_connection=db_connection, organization_id=user_context.organization_id
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    async def _settings(self, project_id: str) -> dict[str, Any]:
        """Load project booking settings after ensuring the project exists."""
        await self.setup_service.ensure_project(project_id=project_id)
        return await self.config_service.get_settings(project_id=project_id)

    async def _require_member(self, *, project_id: str, contact_id: str) -> None:
        """Raise when the contact is not an active project member."""
        in_project = await self.contact_units_repo.contact_has_active_project_membership(
            organization_id=self._org_id,
            contact_id=contact_id,
            project_id=project_id,
        )
        if not in_project:
            raise ValidationException(
                message_key="facility_booking.errors.host_not_in_project",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    def _effective_limit(self, wallet: dict[str, Any], settings: dict[str, Any]) -> int:
        """Return wallet credit limit (custom or project default)."""
        if wallet.get("credit_limit") is not None:
            return int(wallet["credit_limit"])
        return int(settings.get("wallet_credit_limit") or 10000)

    def _serialize_invoice(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map an invoice row to the API response shape."""
        lines = row.get("lines") or []
        return BookingInvoiceResponse(
            id=str(row["id"]),
            contact_id=str(row["contact_id"]),
            contact_name=row.get("contact_name"),
            number=str(row["number"]),
            status=row["status"],
            lines=[InvoiceLineResponse.model_validate(line) for line in lines],
            total=int(row["total"]),
            period_label=str(row["period_label"]),
            due_date=row["due_date"],
            paid_at=row.get("paid_at"),
            paid_via=row.get("paid_via"),
            payment_ref=row.get("payment_ref"),
            collected_by=row.get("collected_by"),
            notes=row.get("notes"),
            created_at=row.get("created_at"),
        ).model_dump(mode="json")

    def _serialize_wallet(
        self,
        wallet: dict[str, Any],
        settings: dict[str, Any],
        transactions: list[dict[str, Any]],
        contact_name: str | None = None,
    ) -> dict[str, Any]:
        """Map wallet row and transactions to the API response shape."""
        return WalletResponse(
            contact_id=str(wallet["contact_id"]),
            contact_name=contact_name or wallet.get("contact_name"),
            balance=int(wallet["balance"]),
            credit_limit=self._effective_limit(wallet, settings),
            custom_limit=wallet.get("credit_limit") is not None,
            transactions=[
                WalletTransactionResponse(
                    id=str(row["id"]),
                    contact_id=str(row["contact_id"]),
                    entry_type=row["entry_type"],
                    amount=int(row["amount"]),
                    method=row.get("method"),
                    description=str(row["description"]),
                    posted_at=row["posted_at"],
                )
                for row in transactions
            ],
        ).model_dump(mode="json")

    async def get_wallet(self, *, project_id: str, contact_id: str) -> dict[str, Any]:
        """Return wallet balance, limit and recent transactions for a contact."""
        settings = await self._settings(project_id)
        await self._require_member(project_id=project_id, contact_id=contact_id)
        wallet = await self.wallets.get_or_create(
            organization_id=self._org_id, project_id=project_id, contact_id=contact_id
        )
        txns = await self.wallets.list_transactions(
            organization_id=self._org_id,
            project_id=project_id,
            contact_id=contact_id,
        )
        return self._serialize_wallet(wallet, settings, txns)

    async def top_up(self, *, project_id: str, body: WalletTopUpRequest) -> dict[str, Any]:
        """Top up a contact wallet using cash or online payment."""
        settings = await self._settings(project_id)
        if body.method == FacilityBookingPaymentMethod.WALLET:
            raise ValidationException(
                message_key="facility_booking.errors.invalid_topup_method",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if body.method == FacilityBookingPaymentMethod.CASH and not settings["cash_enabled"]:
            raise ValidationException(
                message_key="facility_booking.errors.payment_method_disabled",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if body.method == FacilityBookingPaymentMethod.ONLINE and not settings["online_enabled"]:
            raise ValidationException(
                message_key="facility_booking.errors.payment_method_disabled",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self._require_member(project_id=project_id, contact_id=body.contact_id)
        wallet = await self.wallets.get_or_create(
            organization_id=self._org_id, project_id=project_id, contact_id=body.contact_id
        )
        updated = await self.wallets.update_balance(
            wallet_id=str(wallet["id"]), balance=int(wallet["balance"]) + body.amount
        )
        await self.wallets.insert_transaction(
            {
                "organization_id": self._org_id,
                "project_id": project_id,
                "wallet_id": str(wallet["id"]),
                "contact_id": body.contact_id,
                "entry_type": FacilityBookingWalletTxnType.TOPUP.value,
                "amount": body.amount,
                "method": body.method.value,
                "description": "Wallet top-up",
                "created_by_user_id": self.user_context.user_id,
            }
        )
        return await self.get_wallet(project_id=project_id, contact_id=body.contact_id) | {
            "balance": int(updated["balance"])
        }

    async def adjust(self, *, project_id: str, body: WalletAdjustRequest) -> dict[str, Any]:
        """Apply a manual wallet adjustment with credit-limit checks."""
        if body.amount == 0:
            raise ValidationException(
                message_key="facility_booking.errors.invalid_charge_amount",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        settings = await self._settings(project_id)
        await self._require_member(project_id=project_id, contact_id=body.contact_id)
        wallet = await self.wallets.get_or_create(
            organization_id=self._org_id, project_id=project_id, contact_id=body.contact_id
        )
        limit = self._effective_limit(wallet, settings)
        next_balance = int(wallet["balance"]) + body.amount
        if next_balance < -limit:
            raise ValidationException(
                message_key="facility_booking.errors.wallet_credit_exceeded",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self.wallets.update_balance(wallet_id=str(wallet["id"]), balance=next_balance)
        await self.wallets.insert_transaction(
            {
                "organization_id": self._org_id,
                "project_id": project_id,
                "wallet_id": str(wallet["id"]),
                "contact_id": body.contact_id,
                "entry_type": FacilityBookingWalletTxnType.ADJUSTMENT.value,
                "amount": body.amount,
                "description": body.reason.strip(),
                "created_by_user_id": self.user_context.user_id,
            }
        )
        await self.notifier.wallet_updated(
            contact_id=body.contact_id,
            amount=body.amount,
            reason=body.reason.strip(),
        )
        return await self.get_wallet(project_id=project_id, contact_id=body.contact_id)

    async def set_limit(
        self, *, project_id: str, contact_id: str, body: WalletLimitRequest
    ) -> dict[str, Any]:
        """Set or clear a custom credit limit on a contact wallet."""
        await self._settings(project_id)
        await self._require_member(project_id=project_id, contact_id=contact_id)
        wallet = await self.wallets.get_or_create(
            organization_id=self._org_id, project_id=project_id, contact_id=contact_id
        )
        await self.wallets.set_limit(wallet_id=str(wallet["id"]), credit_limit=body.limit)
        return await self.get_wallet(project_id=project_id, contact_id=contact_id)

    async def list_invoices(
        self,
        *,
        project_id: str,
        contact_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List facility booking invoices with optional filters."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows, total = await self.invoices.list_project(
            organization_id=self._org_id,
            project_id=project_id,
            contact_id=contact_id,
            status=status,
            page=page,
            page_size=page_size,
        )
        return [self._serialize_invoice(row) for row in rows], total

    async def get_invoice(
        self, *, project_id: str, invoice_id: str, contact_id: str | None = None
    ) -> dict[str, Any]:
        """Return one invoice, optionally scoped to a contact."""
        await self.setup_service.ensure_project(project_id=project_id)
        row = await self.invoices.get(
            organization_id=self._org_id, project_id=project_id, invoice_id=invoice_id
        )
        if not row:
            raise NotFoundException(
                message_key="facility_booking.errors.invoice_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if contact_id and str(row["contact_id"]) != contact_id:
            raise ForbiddenException(
                message_key="facility_booking.errors.invoice_not_accessible",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        return self._serialize_invoice(row)

    async def generate_invoices(
        self, *, project_id: str, body: GenerateInvoicesRequest
    ) -> dict[str, Any]:
        """Generate invoices from unbilled ledger entries."""
        settings = await self._settings(project_id)
        entries = await self.ledger_repo.list_invoiceable(
            organization_id=self._org_id,
            project_id=project_id,
            contact_ids=body.contact_ids,
        )
        by_contact: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            by_contact[str(entry["contact_id"])].append(entry)
        now = datetime.now(timezone.utc)
        year = now.year
        seq = await self.invoices.count_for_year(
            organization_id=self._org_id, project_id=project_id, year=year
        )
        due = now.date() + timedelta(
            days=INVOICE_FREQUENCY_DAYS.get(str(settings["invoice_frequency"]), 30)
        )
        period_label = now.strftime("%B %Y")
        created = 0
        amount = 0
        invoices: list[dict[str, Any]] = []
        for contact_id, contact_entries in by_contact.items():
            total = sum(int(entry["amount"]) for entry in contact_entries)
            if total <= 0:
                continue
            seq += 1
            invoice = await self.invoices.insert(
                {
                    "organization_id": self._org_id,
                    "project_id": project_id,
                    "contact_id": contact_id,
                    "number": f"INV-{year}-{seq:04d}",
                    "status": FacilityBookingInvoiceStatus.ISSUED.value,
                    "lines": [
                        {
                            "description": entry["description"],
                            "amount": int(entry["amount"]),
                            "reservation_id": entry.get("reservation_id"),
                        }
                        for entry in contact_entries
                    ],
                    "total": total,
                    "period_label": period_label,
                    "due_date": due,
                    "created_by_user_id": self.user_context.user_id,
                }
            )
            await self.ledger_repo.attach_invoice(
                entry_ids=[str(entry["id"]) for entry in contact_entries],
                invoice_id=str(invoice["id"]),
            )
            await self.notifier.invoice_generated(
                contact_id=contact_id,
                invoice_id=str(invoice["id"]),
                number=invoice["number"],
                total=total,
                due_date=due.isoformat(),
            )
            created += 1
            amount += total
            invoices.append(self._serialize_invoice({**invoice, "contact_name": None}))
        return {"created": created, "amount": amount, "invoices": invoices}

    async def pay_invoice(
        self,
        *,
        project_id: str,
        invoice_id: str,
        body: PayInvoiceRequest,
        allowed_methods: set[str] | None = None,
        contact_id: str | None = None,
    ) -> dict[str, Any]:
        """Record payment for an invoice via wallet, cash or online."""
        settings = await self._settings(project_id)
        invoice = await self.invoices.get(
            organization_id=self._org_id, project_id=project_id, invoice_id=invoice_id
        )
        if not invoice:
            raise NotFoundException(
                message_key="facility_booking.errors.invoice_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if contact_id and str(invoice["contact_id"]) != contact_id:
            raise ForbiddenException(
                message_key="facility_booking.errors.invoice_not_accessible",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        if str(invoice["status"]) == FacilityBookingInvoiceStatus.PAID.value:
            raise ValidationException(
                message_key="facility_booking.errors.invoice_already_paid",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        method = body.method.value
        if allowed_methods is not None and method not in allowed_methods:
            raise ValidationException(
                message_key="facility_booking.errors.payment_method_disabled",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        method_ok = (
            (method == FacilityBookingPaymentMethod.WALLET.value and settings["wallet_enabled"])
            or (method == FacilityBookingPaymentMethod.CASH.value and settings["cash_enabled"])
            or (method == FacilityBookingPaymentMethod.ONLINE.value and settings["online_enabled"])
        )
        if not method_ok:
            raise ValidationException(
                message_key="facility_booking.errors.payment_method_disabled",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        total = int(invoice["total"])
        contact_id = str(invoice["contact_id"])
        if method == FacilityBookingPaymentMethod.WALLET.value:
            wallet = await self.wallets.get_or_create(
                organization_id=self._org_id, project_id=project_id, contact_id=contact_id
            )
            limit = self._effective_limit(wallet, settings)
            next_balance = int(wallet["balance"]) - total
            if next_balance < -limit:
                raise ValidationException(
                    message_key="facility_booking.errors.wallet_credit_exceeded",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            await self.wallets.update_balance(wallet_id=str(wallet["id"]), balance=next_balance)
            await self.wallets.insert_transaction(
                {
                    "organization_id": self._org_id,
                    "project_id": project_id,
                    "wallet_id": str(wallet["id"]),
                    "contact_id": contact_id,
                    "entry_type": FacilityBookingWalletTxnType.PAYMENT.value,
                    "amount": -total,
                    "method": method,
                    "description": str(invoice["number"]),
                    "created_by_user_id": self.user_context.user_id,
                }
            )
        now = datetime.now(timezone.utc)
        via = {
            FacilityBookingPaymentMethod.WALLET.value: "member wallet",
            FacilityBookingPaymentMethod.CASH.value: "cash at location",
        }.get(method, "online payment")
        ref = body.reference or (
            f"ONLINE-{format(int(now.timestamp() * 1000), 'X')}"
            if method == FacilityBookingPaymentMethod.ONLINE.value
            else f"RCPT-{format(int(now.timestamp() * 1000), 'X')}"
        )
        paid = await self.invoices.mark_paid(
            invoice_id=invoice_id,
            update={
                "paid_at": now,
                "paid_via": method,
                "payment_ref": ref,
                "collected_by": body.collected_by,
                "notes": body.notes,
            },
        )
        await self.ledger.post(
            project_id=project_id,
            contact_id=contact_id,
            invoice_id=invoice_id,
            entry_type=FacilityBookingLedgerType.PAYMENT.value,
            description=f"{invoice['number']} — paid via {via}",
            amount=-total,
            method=method,
        )
        await self.notifier.payment_received(
            contact_id=contact_id,
            invoice_id=invoice_id,
            number=str(invoice["number"]),
            method=method,
        )
        return self._serialize_invoice({**paid, "contact_name": invoice.get("contact_name")})

    async def overview(self, *, project_id: str) -> dict[str, Any]:
        """Return billing overview metrics for staff dashboards."""
        await self._settings(project_id)
        unbilled = await self.ledger.unbilled(project_id=project_id)
        invoices, _ = await self.list_invoices(project_id=project_id, page=1, page_size=100)
        issued = [
            item for item in invoices if item["status"] == FacilityBookingInvoiceStatus.ISSUED.value
        ]
        now = datetime.now(timezone.utc)
        payments = await self.ledger_repo.list_payments_for_month(
            organization_id=self._org_id,
            project_id=project_id,
            year=now.year,
            month=now.month,
        )
        collected_by_method = {
            FacilityBookingPaymentMethod.WALLET.value: 0,
            FacilityBookingPaymentMethod.CASH.value: 0,
            FacilityBookingPaymentMethod.ONLINE.value: 0,
        }
        for payment in payments:
            method = payment.get("method")
            if method in collected_by_method:
                collected_by_method[method] += -int(payment["amount"])
        wallets = await self.wallets.list_project(
            organization_id=self._org_id, project_id=project_id
        )
        return PaymentOverviewResponse(
            unbilled_total=int(unbilled["unbilled_total"]),
            unbilled_contacts=int(unbilled["unbilled_contacts"]),
            outstanding_total=sum(int(item["total"]) for item in issued),
            outstanding_count=len(issued),
            collected_this_month=sum(collected_by_method.values()),
            wallet_float=sum(int(row["balance"]) for row in wallets),
            collected_by_method=collected_by_method,
            invoices=invoices,
            wallet_balances={str(row["contact_id"]): int(row["balance"]) for row in wallets},
        ).model_dump(mode="json")
