"""Daily fee invoice run. Dkron supplies the clock; this job supplies the rules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg

from apps.user_service.app.services.fee_billing_service import FeeBillingService
from apps.user_service.app.services.fee_invoice_mail import (
    collect_fee_invoice_messages,
    collect_fee_invoice_reminders,
    dispatch_fee_invoice_emails,
)
from apps.user_service.app.services.fee_invoice_reminders import (
    claim_due_fee_invoice_reminders,
)
from libs.shared_db.drivers.asyncpg_uow import UnitOfWork
from libs.shared_utils.logger import get_logger

logger = get_logger("issue_due_fee_invoices")


async def run_due_fee_invoices() -> None:
    """Issue today's invoices, then send the issue and reminder mail."""
    try:
        async with UnitOfWork() as connection:
            result, notices, reminders = await issue_due_fee_invoices(connection)
            messages = await collect_fee_invoice_messages(connection, notices)
            messages.extend(await collect_fee_invoice_reminders(connection, reminders))
        if messages:
            dispatch_fee_invoice_emails(messages)
        logger.info(
            "due fee invoice run finished run_date=%s invoices=%s reminders=%s",
            result.get("run_date"),
            len(result.get("invoice_ids") or []),
            len(reminders),
        )
    except Exception:
        logger.exception("due fee invoice run failed")


async def issue_due_fee_invoices(
    db_connection: asyncpg.Connection,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Issue invoices due today, and return new invoices plus today's reminders."""
    run_date = datetime.now(timezone.utc).date()
    service = FeeBillingService(db_connection)
    result = await service.issue_due(run_date=run_date)
    reminders = await claim_due_fee_invoice_reminders(db_connection, today=run_date)
    return result, service.outbound, reminders
