"""Push a new fee invoice or a pre-due reminder to the people on the unit."""

from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg

from apps.user_service.app.services.fee_invoice_mail import list_invoice_residents
from apps.user_service.app.services.fee_invoice_pdf import format_rupees
from apps.user_service.app.services.push_notification_dispatch import (
    PushNotificationDispatcher,
    recipient_language_from_contact,
)
from libs.shared_utils.logger import get_logger

logger = get_logger("fee_invoice_push")

_NOTIFICATION_TYPE = "NOTIFICATION_TYPE_SYSTEM"
_FEED_TYPE = "fee_invoice"
_ISSUED = "issued"
_REMINDER = "reminder"


def push_recipients(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """One app user per owner, tenant, or family member, with their language."""
    seen: set[str] = set()
    people: list[tuple[str, str]] = []
    for row in rows:
        user_id = str(row.get("user_id") or "").strip()
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        language = recipient_language_from_contact(row.get("additional_data"))
        people.append((user_id, language))
    return people


async def dispatch_fee_invoice_pushes(
    connection: asyncpg.Connection,
    issued: list[dict[str, Any]],
    reminders: list[dict[str, Any]],
    dispatcher: PushNotificationDispatcher | None = None,
) -> None:
    """Push each new invoice and each claimed reminder. One failure is logged."""
    sender = dispatcher or PushNotificationDispatcher(db_connection=connection)
    for notice in issued:
        await _notify(connection, sender, notice, kind=_ISSUED)
    for notice in reminders:
        await _notify(connection, sender, notice, kind=_REMINDER)


async def notify_invoice_push(
    dispatcher: PushNotificationDispatcher,
    rows: list[dict[str, Any]],
    notice: dict[str, Any],
    *,
    kind: str,
) -> None:
    """Push one invoice event to each resident who has an app login."""
    invoice_id = str(notice["invoice_id"])
    amount = format_rupees(notice["total_amount"])
    due_on = _date_text(notice["due_date"])
    params = {
        "invoice_number": str(notice["invoice_number"]),
        "amount": amount,
        "due_date": due_on,
    }
    data = {
        "invoice_id": invoice_id,
        "invoice_number": params["invoice_number"],
        "unit_id": str(notice["unit_id"]),
        "project_id": str(notice["project_id"]),
        "screen": "fee_invoice_detail",
    }
    suffix = _date_text(notice.get("remind_on")) if kind == _REMINDER else _ISSUED
    options = {"idempotency_key": f"fee_invoice:{invoice_id}:{suffix}"}
    for user_id, language in push_recipients(rows):
        try:
            await dispatcher.send_to_user(
                organization_id=str(notice["organization_id"]),
                recipient_user_id=user_id,
                message_key=f"notifications.push.fee_invoice.{kind}",
                notification_type=_NOTIFICATION_TYPE,
                feed_type=_FEED_TYPE,
                language=language,
                params=params,
                data=data,
                entity={"kind": "fee_invoice", "id": invoice_id},
                options=options,
            )
        except Exception:
            logger.exception(
                "fee invoice push failed invoice_id=%s user_id=%s",
                invoice_id,
                user_id,
            )


async def _notify(
    connection: asyncpg.Connection,
    dispatcher: PushNotificationDispatcher,
    notice: dict[str, Any],
    *,
    kind: str,
) -> None:
    """Load the unit's residents and push. A lookup failure is logged."""
    try:
        rows = await list_invoice_residents(
            connection,
            organization_id=str(notice["organization_id"]),
            unit_id=str(notice["unit_id"]),
        )
        await notify_invoice_push(dispatcher, rows, notice, kind=kind)
    except Exception:
        logger.exception(
            "fee invoice push failed invoice_number=%s",
            notice.get("invoice_number"),
        )


def _date_text(value: date | str | None) -> str:
    """ISO date text for the push body and the idempotency key."""
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")
