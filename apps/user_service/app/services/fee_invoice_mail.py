"""Email a new fee invoice, with its PDF, to the people on the unit."""

from __future__ import annotations

import base64
import json
from datetime import date
from typing import Any

import asyncpg

from apps.user_service.app.services.fee_invoice_pdf import (
    build_fee_invoice_pdf,
    format_rupees,
)
from apps.user_service.app.services.fee_invoice_storage import (
    load_fee_invoice_pdf,
    store_fee_invoice_pdf,
)
from apps.user_service.app.utils.email_utils import send_templated_email
from apps.user_service.app.utils.unit_list_serialization import (
    format_primary_contact_email,
)
from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.logger import get_logger

logger = get_logger("fee_invoice_mail")


def recipient_addresses(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Primary email for each owner, tenant, or family member, one address once."""
    seen: set[str] = set()
    people: list[tuple[str, str]] = []
    for row in rows:
        email = format_primary_contact_email(_emails(row.get("emails")))
        if not email:
            continue
        key = email.casefold()
        if key in seen:
            continue
        seen.add(key)
        name = str(row.get("first_name") or "").strip() or "there"
        people.append((name, email))
    return people


async def prepare_fee_invoice_messages(
    connection: asyncpg.Connection,
    notice: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build one message per resident email, with the invoice PDF attached."""
    context = await _unit_context(
        connection,
        project_id=notice["project_id"],
        unit_id=notice["unit_id"],
    )
    people = recipient_addresses(
        await _resident_rows(
            connection,
            organization_id=notice["organization_id"],
            unit_id=notice["unit_id"],
        )
    )
    payload = {**notice, **context}
    pdf = build_fee_invoice_pdf(payload)
    await _save_pdf(connection, notice, pdf)
    if not people:
        return []
    return _messages(
        people,
        notice=notice,
        payload=payload,
        pdf=pdf,
        template="fee_invoice_issued",
    )


async def prepare_fee_invoice_reminder(
    connection: asyncpg.Connection,
    notice: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build one reminder per resident email, attaching the stored PDF."""
    people = recipient_addresses(
        await _resident_rows(
            connection,
            organization_id=notice["organization_id"],
            unit_id=notice["unit_id"],
        )
    )
    if not people:
        return []
    pdf = None
    object_key = notice.get("pdf_path")
    if object_key:
        pdf = await load_fee_invoice_pdf(str(object_key))
    if pdf is None:
        pdf = build_fee_invoice_pdf(notice)
        await _save_pdf(connection, notice, pdf)
    return _messages(
        people,
        notice=notice,
        payload=notice,
        pdf=pdf,
        template="fee_invoice_reminder",
    )


def _messages(
    people: list[tuple[str, str]],
    *,
    notice: dict[str, Any],
    payload: dict[str, Any],
    pdf: bytes,
    template: str,
) -> list[dict[str, Any]]:
    """One templated message per address, with the PDF attached."""
    attachment = {
        "filename": f"{notice['invoice_number']}.pdf",
        "content": base64.b64encode(pdf).decode("ascii"),
    }
    return [
        {
            "email": email,
            "template": template,
            "body_context": {
                "first_name": first_name,
                "invoice_number": str(notice["invoice_number"]),
                "unit_code": str(payload["unit_code"]),
                "project_name": str(payload["project_name"]),
                "total_amount": str(notice["total_amount"]),
                "due_date": str(notice["due_date"]),
                "app_name": shared_settings.app_name,
            },
            "attachments": [attachment],
        }
        for first_name, email in people
    ]


async def _save_pdf(
    connection: asyncpg.Connection,
    notice: dict[str, Any],
    pdf: bytes,
) -> None:
    """Upload the PDF and keep its path on the invoice."""
    invoice_id = notice.get("invoice_id")
    if not invoice_id or notice.get("pdf_path"):
        return
    object_key = await store_fee_invoice_pdf(str(invoice_id), pdf)
    if object_key is None:
        return
    await connection.execute(
        """
        UPDATE fee_invoices
        SET pdf_path = $2
        WHERE id = $1::uuid
          AND pdf_path IS NULL
        """,
        invoice_id,
        object_key,
    )
    notice["pdf_path"] = object_key


async def collect_fee_invoice_messages(
    connection: asyncpg.Connection,
    notices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prepare every new invoice email. One invoice's failure is logged."""
    messages: list[dict[str, Any]] = []
    for notice in notices:
        try:
            messages.extend(await prepare_fee_invoice_messages(connection, notice))
        except Exception:
            logger.exception(
                "fee invoice email failed invoice_number=%s",
                notice.get("invoice_number"),
            )
    return messages


async def collect_fee_invoice_reminders(
    connection: asyncpg.Connection,
    notices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prepare every reminder email. One invoice's failure is logged."""
    messages: list[dict[str, Any]] = []
    for notice in notices:
        try:
            messages.extend(await prepare_fee_invoice_reminder(connection, notice))
        except Exception:
            logger.exception(
                "fee invoice reminder failed invoice_number=%s",
                notice.get("invoice_number"),
            )
    return messages


_PAYMENT_MODES = {
    "cash": "Cash",
    "cheque": "Cheque",
    "neft_rtgs": "NEFT/RTGS",
    "card": "Card",
    "upi": "UPI",
    "other": "Other",
}
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def payment_mode_label(mode: str) -> str:
    """Resident-facing name for a stored payment mode."""
    return _PAYMENT_MODES.get(mode, mode)


def payment_reference(value: str | None) -> str:
    """Reference text, or a dash when the receipt has none."""
    text = (value or "").strip()
    return text or "—"


async def prepare_fee_payment_messages(
    connection: asyncpg.Connection,
    notice: dict[str, Any],
) -> list[dict[str, Any]]:
    """One acknowledgment per resident email. The transactional layout wraps it."""
    context = await _payment_context(
        connection,
        organization_id=notice["organization_id"],
        project_id=notice["project_id"],
        invoice_id=notice["invoice_id"],
    )
    if context is None:
        return []
    people = recipient_addresses(
        await _resident_rows(
            connection,
            organization_id=notice["organization_id"],
            unit_id=context["unit_id"],
        )
    )
    received_on = _long_date(str(notice["paid_on"]))
    return [
        {
            "email": email,
            "template": "fee_invoice_payment",
            "body_context": {
                "first_name": first_name,
                "invoice_number": context["invoice_number"],
                "unit_code": context["unit_code"],
                "project_name": context["project_name"],
                "received_amount": format_rupees(notice["amount"]),
                "received_on": received_on,
                "mode": payment_mode_label(str(notice["mode"])),
                "reference": payment_reference(notice.get("reference")),
                "outstanding": format_rupees(notice["outstanding"]),
                "app_name": shared_settings.app_name,
            },
        }
        for first_name, email in people
    ]


async def collect_fee_payment_messages(
    connection: asyncpg.Connection,
    notice: dict[str, Any],
) -> list[dict[str, Any]]:
    """Prepare the payment acknowledgment. A lookup failure is logged."""
    try:
        return await prepare_fee_payment_messages(connection, notice)
    except Exception:
        logger.exception(
            "fee payment email failed invoice_id=%s",
            notice.get("invoice_id"),
        )
        return []


def _long_date(value: str) -> str:
    """5 October 2026 from an ISO date."""
    day = date.fromisoformat(value[:10])
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


async def _payment_context(
    connection: asyncpg.Connection,
    *,
    organization_id: str,
    project_id: str,
    invoice_id: str,
) -> dict[str, str] | None:
    """Invoice number, unit, and project for the acknowledgment."""
    row = await connection.fetchrow(
        """
        SELECT i.unit_id, i.invoice_number, u.code AS unit_code, p.name AS project_name
        FROM fee_invoices AS i
        JOIN units AS u ON u.id = i.unit_id
        JOIN projects AS p ON p.id = i.project_id
        WHERE i.id = $1::uuid
          AND i.organization_id = $2::uuid
          AND i.project_id = $3::uuid
        """,
        invoice_id,
        organization_id,
        project_id,
    )
    if row is None:
        return None
    return {
        "unit_id": str(row["unit_id"]),
        "invoice_number": str(row["invoice_number"]),
        "unit_code": str(row["unit_code"]).strip(),
        "project_name": str(row["project_name"]),
    }


def dispatch_fee_invoice_emails(messages: list[dict[str, Any]]) -> None:
    """Send prepared invoice emails. One failure does not stop the rest."""
    for message in messages:
        try:
            send_templated_email(
                email=message["email"],
                template=message["template"],
                body_context=message["body_context"],
                attachments=message.get("attachments"),
            )
        except Exception:
            logger.exception(
                "fee invoice email failed invoice_number=%s",
                message.get("body_context", {}).get("invoice_number"),
            )


def _emails(value: Any) -> Any:
    """JSONB emails as a list. asyncpg sometimes returns the raw text."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return value


async def _unit_context(
    connection: asyncpg.Connection,
    *,
    project_id: str,
    unit_id: str,
) -> dict[str, str]:
    """Project name and unit code for the letter and the PDF."""
    row = await connection.fetchrow(
        """
        SELECT p.name AS project_name, u.code AS unit_code
        FROM projects p
        JOIN units u ON u.project_id = p.id
        WHERE p.id = $1::uuid
          AND u.id = $2::uuid
        """,
        project_id,
        unit_id,
    )
    if row is None:
        return {"project_name": "", "unit_code": ""}
    return {
        "project_name": str(row["project_name"]),
        "unit_code": str(row["unit_code"]).strip(),
    }


async def _resident_rows(
    connection: asyncpg.Connection,
    *,
    organization_id: str,
    unit_id: str,
) -> list[dict[str, Any]]:
    """Owner, tenant, and family contacts currently linked to the unit."""
    rows = await connection.fetch(
        """
        SELECT c.first_name, c.emails
        FROM contact_units cu
        JOIN contacts c
          ON c.id = cu.contact_id
         AND c.organization_id = cu.organization_id
        JOIN contact_roles cr
          ON cr.organization_id = cu.organization_id
         AND cr.contact_id = cu.contact_id
         AND cr.unit_id = cu.unit_id
         AND cr.status = 'active'::public.contact_role_status
         AND cr.ended_at IS NULL
         AND cr.role_type IN (
             'Owner'::public.contact_role_type,
             'Tenant'::public.contact_role_type,
             'Family'::public.contact_role_type
         )
        WHERE cu.organization_id = $1::uuid
          AND cu.unit_id = $2::uuid
          AND cu.status IN (
              'active'::contact_unit_status,
              'pending'::contact_unit_status
          )
          AND c.status = 'active'
        """,
        organization_id,
        unit_id,
    )
    return [dict(row) for row in rows]
