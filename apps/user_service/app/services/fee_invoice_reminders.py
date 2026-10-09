"""Pre-due fee invoice reminders from the project's finance settings."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import asyncpg

from apps.user_service.app.services.fee_configuration_labels import money_str


def reminder_dates(*, due_on: date, count: int, interval_days: int) -> list[date]:
    """Dates before the due date, spaced by the project's reminder interval.

    Two reminders, three days apart, due on 15 October, are 9 October and 12 October.
    """
    if count < 1 or interval_days < 1:
        return []
    return [due_on - timedelta(days=interval_days * step) for step in range(count, 0, -1)]


def is_reminder_day(
    *,
    due_on: date,
    count: int,
    interval_days: int,
    today: date,
) -> bool:
    """True when today is one of the scheduled reminder dates."""
    return today in reminder_dates(due_on=due_on, count=count, interval_days=interval_days)


async def claim_due_fee_invoice_reminders(
    connection: asyncpg.Connection,
    *,
    today: date,
) -> list[dict[str, Any]]:
    """Claim unpaid invoices whose pre-due reminder falls on today."""
    rows = await connection.fetch(_DUE_SQL, today)
    claimed: list[dict[str, Any]] = []
    for row in rows:
        invoice = dict(row)
        due_on = invoice["due_date"]
        if not is_reminder_day(
            due_on=due_on,
            count=int(invoice["pre_due_reminder_count"]),
            interval_days=int(invoice["pre_due_reminder_interval_days"]),
            today=today,
        ):
            continue
        if not await _claim(connection, invoice, today):
            continue
        notice = _notice(invoice)
        notice["remind_on"] = today.isoformat()
        claimed.append(notice)
    return claimed


async def _claim(
    connection: asyncpg.Connection,
    invoice: dict[str, Any],
    today: date,
) -> bool:
    """Record this reminder day once. A repeat run the same day claims nothing."""
    row = await connection.fetchrow(
        """
        INSERT INTO fee_invoice_reminders (
            organization_id,
            project_id,
            invoice_id,
            remind_on
        )
        VALUES ($1::uuid, $2::uuid, $3::uuid, $4::date)
        ON CONFLICT (invoice_id, remind_on) DO NOTHING
        RETURNING id
        """,
        invoice["organization_id"],
        invoice["project_id"],
        invoice["id"],
        today,
    )
    return row is not None


def _notice(invoice: dict[str, Any]) -> dict[str, Any]:
    """Shape shared with the issued-invoice mail."""
    return {
        "invoice_id": str(invoice["id"]),
        "organization_id": str(invoice["organization_id"]),
        "project_id": str(invoice["project_id"]),
        "unit_id": str(invoice["unit_id"]),
        "invoice_number": invoice["invoice_number"],
        "unit_code": str(invoice["unit_code"]).strip(),
        "project_name": str(invoice["project_name"]),
        "pdf_path": invoice.get("pdf_path"),
        "billing_month": invoice["billing_month"].isoformat(),
        "invoice_date": invoice["invoice_date"].isoformat(),
        "due_date": invoice["due_date"].isoformat(),
        "taxable_amount": money_str(invoice["taxable_amount"]),
        "tax_amount": money_str(invoice["tax_amount"]),
        "round_off_amount": money_str(invoice["round_off_amount"]),
        "total_amount": money_str(invoice["total_amount"]),
        "lines": _lines(invoice.get("lines")),
    }


def _lines(value: Any) -> list[dict[str, str | None]]:
    """Invoice lines as money strings. asyncpg may return JSON as text."""
    if isinstance(value, str):
        value = json.loads(value)
    return [
        {
            "description": str(line["description"]),
            "kind": str(line.get("kind") or ""),
            "area_or_quantity": _optional_money_text(line.get("area_or_quantity")),
            "rate": _optional_money_text(line.get("rate")),
            "taxable_amount": money_str(line["taxable_amount"]),
            "tax_amount": money_str(line["tax_amount"]),
            "line_total": money_str(line["line_total"]),
        }
        for line in value or []
    ]


def _optional_money_text(value: Any) -> str | None:
    """A money string, or nothing when the line has no area or rate."""
    if value is None or value == "":
        return None
    return money_str(value)


_DUE_SQL = """
SELECT
    i.id,
    i.organization_id,
    i.project_id,
    i.unit_id,
    i.invoice_number,
    i.billing_month,
    i.invoice_date,
    i.due_date,
    i.taxable_amount,
    i.tax_amount,
    i.round_off_amount,
    i.total_amount,
    i.pdf_path,
    u.code AS unit_code,
    p.name AS project_name,
    s.pre_due_reminder_count,
    s.pre_due_reminder_interval_days,
    COALESCE(
        (
            SELECT json_agg(
                json_build_object(
                    'description', line.description,
                    'kind', line.kind,
                    'area_or_quantity', line.area_or_quantity,
                    'rate', line.rate,
                    'taxable_amount', line.taxable_amount,
                    'tax_amount', line.tax_amount,
                    'line_total', line.line_total
                )
                ORDER BY line.created_at
            )
            FROM fee_invoice_lines AS line
            WHERE line.invoice_id = i.id
        ),
        '[]'::json
    ) AS lines
FROM fee_invoices AS i
JOIN finance_settings AS s
    ON s.project_id = i.project_id
   AND s.organization_id = i.organization_id
JOIN units AS u ON u.id = i.unit_id
JOIN projects AS p ON p.id = i.project_id
WHERE i.status IN ('issued', 'partial')
  AND i.due_date > $1::date
  AND s.pre_due_reminder_count > 0
  AND NOT EXISTS (
      SELECT 1
      FROM fee_invoice_reminders AS reminder
      WHERE reminder.invoice_id = i.id
        AND reminder.remind_on = $1::date
  )
"""
