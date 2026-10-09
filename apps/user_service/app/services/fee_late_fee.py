"""Late fee added on the next invoice, from what was still unpaid after the due date."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from apps.user_service.app.services.fee_configuration_labels import (
    half_up_rupee,
    money,
    money_str,
)

_ZERO = Decimal("0.00")


def started_months(due_date: date, as_of: date) -> int:
    """Months overdue. The day after the due date starts month one."""
    if as_of <= due_date:
        return 0
    months = (as_of.year - due_date.year) * 12 + (as_of.month - due_date.month)
    if as_of.day > due_date.day:
        months += 1
    return max(months, 1)


def days_overdue(due_date: date, as_of: date) -> int:
    """Days after the due date. The due date itself is not overdue."""
    if as_of <= due_date:
        return 0
    return (as_of - due_date).days


def late_fee_drafts(
    invoices: list[dict[str, Any]], run_date: date
) -> dict[str, list[dict[str, Any]]]:
    """Late-fee lines for the run date, grouped by unit."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for invoice in invoices:
        if invoice["invoice_date"] >= run_date or invoice["due_date"] >= run_date:
            continue
        lines = _lines_for_invoice(invoice, run_date)
        if lines:
            grouped.setdefault(str(invoice["unit_id"]), []).extend(lines)
    return grouped


def invoice_status(stored: str, due_on: date, as_of: date) -> str:
    """Overdue when the due date has passed and the invoice is still open."""
    if stored in {"paid", "cancelled"}:
        return stored
    if due_on < as_of:
        return "overdue"
    return stored


def billing_today() -> date:
    """The UTC calendar day used for overdue."""
    return datetime.now(timezone.utc).date()


def build_unit_balance(
    invoices: list[dict[str, Any]],
    *,
    credit: Decimal = _ZERO,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Arrears, late fee, current charges, and the amount still unpaid."""
    as_of = billing_today() if as_of is None else as_of
    open_invoices = [invoice for invoice in invoices if _is_open(invoice)]
    open_invoices.sort(
        key=lambda invoice: (
            invoice["billing_month"],
            invoice["invoice_date"],
            str(invoice["id"]),
        )
    )
    latest = open_invoices[-1] if open_invoices else None
    arrears = _ZERO
    for invoice in open_invoices:
        if latest is not None and invoice["id"] != latest["id"]:
            arrears = money(arrears + _outstanding(invoice))
    late_fee = _ZERO
    current = _ZERO
    if latest is not None:
        for line in latest.get("lines") or []:
            amount = money(line["line_total"])
            if line.get("line_role") == "late_fee":
                late_fee = money(late_fee + amount)
            else:
                current = money(current + amount)
    owed = money(sum((_outstanding(invoice) for invoice in open_invoices), _ZERO))
    unapplied = money(credit)
    return {
        "amount_due": money_str(money(owed - unapplied)),
        "credit": money_str(unapplied),
        "arrears": money_str(arrears),
        "late_fee": money_str(late_fee),
        "current_charges": money_str(current),
        "invoices": [_invoice_view(invoice, as_of) for invoice in open_invoices],
    }


def build_outstanding_summary(
    invoices: list[dict[str, Any]],
    *,
    credit: Decimal = _ZERO,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Total still to pay, for the resident pending-payments card."""
    as_of = billing_today() if as_of is None else as_of
    open_invoices = [invoice for invoice in invoices if _is_open(invoice)]
    open_invoices.sort(
        key=lambda invoice: (
            invoice["billing_month"],
            invoice["invoice_date"],
            str(invoice["id"]),
        )
    )
    owed = money(sum((_outstanding(invoice) for invoice in open_invoices), _ZERO))
    payable = money(owed - money(credit))
    if payable < 0:
        payable = _ZERO
    months: list[str] = []
    for invoice in open_invoices:
        month = _iso(invoice["billing_month"])
        if month not in months:
            months.append(month)
    includes_late_fee = any(
        line.get("line_role") == "late_fee" and money(line["line_total"]) > 0
        for invoice in open_invoices
        for line in invoice.get("lines") or []
    )
    overdue = any(
        invoice_status(invoice.get("status") or "issued", invoice["due_date"], as_of) == "overdue"
        for invoice in open_invoices
    )
    return {
        "total_outstanding": money_str(payable),
        "overdue": overdue,
        "unpaid_count": len(open_invoices),
        "billing_months": months,
        "includes_late_fee": includes_late_fee,
    }


def build_collection_summary(
    rows: list[dict[str, Any]],
    *,
    as_of: date,
) -> dict[str, Any]:
    """Invoiced, collected, outstanding, and overdue totals for the cards."""
    invoiced = _ZERO
    collected = _ZERO
    outstanding = _ZERO
    overdue_amount = _ZERO
    invoice_count = 0
    open_count = 0
    overdue_count = 0
    for row in rows:
        if row.get("status") == "cancelled":
            continue
        total = money(row["total_amount"])
        paid = money(row.get("amount_paid") or 0)
        due = money(total - paid)
        if due < 0:
            due = _ZERO
        invoiced = money(invoiced + total)
        collected = money(collected + paid)
        invoice_count += 1
        if due <= 0:
            continue
        outstanding = money(outstanding + due)
        open_count += 1
        due_on = row["due_date"]
        if row.get("status") in {"issued", "partial"} and due_on < as_of:
            overdue_amount = money(overdue_amount + due)
            overdue_count += 1
    if invoiced == 0:
        percent = 0
    else:
        share = (collected * Decimal(100) / invoiced).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
        percent = int(share)
    return {
        "invoiced_amount": money_str(invoiced),
        "invoice_count": invoice_count,
        "collected_amount": money_str(collected),
        "collected_percent": percent,
        "outstanding_amount": money_str(outstanding),
        "open_count": open_count,
        "overdue_count": overdue_count,
        "overdue_amount": money_str(overdue_amount),
    }


def plan_credit_applications(
    invoices: list[dict[str, Any]], credit_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Match unapplied credit to open invoices, oldest credit and oldest invoice first."""
    pools = [
        {
            "id": str(row["id"]),
            "left": money(row["amount"]),
            "paid_on": row["paid_on"],
            "mode": row["mode"],
            "reference": row.get("reference"),
        }
        for row in credit_rows
        if money(row["amount"]) > 0
    ]
    pools.sort(key=lambda row: (row["paid_on"], row["id"]))
    ordered = sorted(
        invoices,
        key=lambda invoice: (
            invoice["billing_month"],
            invoice["invoice_date"],
            str(invoice["id"]),
        ),
    )
    applications: list[dict[str, Any]] = []
    for invoice in ordered:
        due = _outstanding(invoice)
        if due <= 0:
            continue
        for pool in pools:
            if due <= 0 or pool["left"] <= 0:
                continue
            take = pool["left"] if pool["left"] <= due else due
            applications.append(
                {
                    "credit_id": pool["id"],
                    "invoice_id": str(invoice["id"]),
                    "organization_id": str(invoice["organization_id"]),
                    "project_id": str(invoice["project_id"]),
                    "amount": take,
                    "paid_on": pool["paid_on"],
                    "mode": pool["mode"],
                    "reference": pool["reference"],
                }
            )
            pool["left"] = money(pool["left"] - take)
            due = money(due - take)
    return applications


def _lines_for_invoice(invoice: dict[str, Any], run_date: date) -> list[dict[str, Any]]:
    """Incremental late-fee lines for one earlier invoice."""
    slices = _overdue_slices(invoice, run_date)
    if not slices:
        return []
    drafts: list[dict[str, Any]] = []
    for line in invoice_charge_lines(invoice):
        draft = _line_draft(invoice, line, slices)
        if draft is not None:
            drafts.append(draft)
    return drafts


def _line_draft(
    invoice: dict[str, Any],
    line: dict[str, Any],
    slices: list[tuple[Decimal, date]],
) -> dict[str, Any] | None:
    """One late-fee line for a charge line, or None when nothing new is owed."""
    late_fee = line.get("late_fee") or {"mode": "none"}
    if str(late_fee.get("mode") or "none") == "none":
        return None
    target, months, days = _line_target(invoice, line, slices)
    increment = money(target - _already_posted(invoice, str(line["id"])))
    if increment <= 0:
        return None
    description = f"Late fee — {line.get('description') or 'Fee'}"[:240]
    return {
        "fee_head_id": str(line["fee_head_id"]),
        "kind": line["kind"],
        "fee_head_version": int(line["fee_head_version"]),
        "description": description,
        "area_or_quantity": None,
        "rate": increment,
        "minimum_amount": None,
        "taxable_amount": increment,
        "tax_amount": _ZERO,
        "line_total": increment,
        "due_within_days": 0,
        "line_role": "late_fee",
        "source_invoice_id": str(invoice["id"]),
        "source_line_id": str(line["id"]),
        "started_months": months,
        "days_overdue": days,
    }


def _line_target(
    invoice: dict[str, Any],
    line: dict[str, Any],
    slices: list[tuple[Decimal, date]],
) -> tuple[Decimal, int, int]:
    """Late fee for one charge line across the overdue slices."""
    charge_lines = invoice_charge_lines(invoice)
    index = next(pos for pos, item in enumerate(charge_lines) if item["id"] == line["id"])
    late_fee = line.get("late_fee") or {"mode": "none"}
    due_date = invoice["due_date"]
    as_of = max(moment for _, moment in slices)
    if str(late_fee.get("mode") or "none") == "flat":
        share = _shares([item["line_total"] for item in charge_lines], slices_total(slices))[index]
        if share <= 0:
            return _ZERO, 0, 0
        return _target(late_fee, share, due_date, as_of)
    target = _ZERO
    months = 0
    for amount, moment in slices:
        share = _shares([item["line_total"] for item in charge_lines], amount)[index]
        result = _target(late_fee, share, due_date, moment)
        target = money(target + result[0])
        months = max(months, result[1])
    return target, months, days_overdue(due_date, as_of)


def invoice_charge_lines(invoice: dict[str, Any]) -> list[dict[str, Any]]:
    """Charge lines on an invoice, ignoring late-fee lines."""
    return [
        line for line in invoice.get("lines") or [] if line.get("line_role", "charge") == "charge"
    ]


def slices_total(slices: list[tuple[Decimal, date]]) -> Decimal:
    """Sum of the overdue slices."""
    return money(sum((amount for amount, _ in slices), _ZERO))


def _target(
    late_fee: dict[str, Any], base: Decimal, due_date: date, as_of: date
) -> tuple[Decimal, int, int]:
    """Full late fee for this base as of the clock date, plus the month and day counts."""
    days = days_overdue(due_date, as_of)
    months = started_months(due_date, as_of)
    mode = str(late_fee.get("mode") or "none")
    if mode == "flat":
        return _flat_amount(late_fee.get("steps") or [], days), months, days
    if mode == "interest":
        annual = money(late_fee.get("annual_percent") or 0)
        monthly = half_up_rupee(base * annual / Decimal("100") / Decimal("12"))
        return money(monthly * months), months, days
    return _ZERO, months, days


def _flat_amount(steps: list[dict[str, Any]], days: int) -> Decimal:
    """The step the bill has reached. Earlier steps are not added on top."""
    reached = _ZERO
    ordered = sorted(steps, key=lambda step: int(step["days_overdue"]))
    for step in ordered:
        if days >= int(step["days_overdue"]):
            reached = money(step["amount"])
    return reached


def _overdue_slices(invoice: dict[str, Any], run_date: date) -> list[tuple[Decimal, date]]:
    """Amounts unpaid after the due date, each with the date its clock stops."""
    on_time = _paid_on_or_before(invoice, invoice["due_date"])
    remaining = money(money(invoice["total_amount"]) - on_time)
    if remaining <= 0:
        return []
    slices: list[tuple[Decimal, date]] = []
    late_payments = sorted(
        (
            payment
            for payment in invoice.get("payments") or []
            if payment["paid_on"] > invoice["due_date"]
        ),
        key=lambda payment: payment["paid_on"],
    )
    for payment in late_payments:
        take = min(money(payment["amount"]), remaining)
        if take > 0:
            slices.append((take, payment["paid_on"]))
            remaining = money(remaining - take)
    if remaining > 0:
        slices.append((remaining, run_date))
    return slices


def _shares(weights: list[Any], amount: Decimal) -> list[Decimal]:
    """Split an amount across lines. The last line takes the remainder."""
    parsed = [money(weight) for weight in weights]
    weight_sum = money(sum(parsed, _ZERO))
    if weight_sum <= 0 or amount <= 0:
        return [_ZERO for _ in parsed]
    shares: list[Decimal] = []
    used = _ZERO
    for index, weight in enumerate(parsed):
        if index == len(parsed) - 1:
            shares.append(money(amount - used))
        else:
            share = money(amount * weight / weight_sum)
            shares.append(share)
            used = money(used + share)
    return shares


def _already_posted(invoice: dict[str, Any], source_line_id: str) -> Decimal:
    """Late fee already written for this charge line on earlier invoices."""
    posted = invoice.get("late_fees_posted") or []
    return money(
        sum(
            (
                money(row["line_total"])
                for row in posted
                if str(row.get("source_line_id")) == source_line_id
            ),
            _ZERO,
        )
    )


def _paid_on_or_before(invoice: dict[str, Any], day: date) -> Decimal:
    """Payments dated on or before a day."""
    return money(
        sum(
            (
                money(payment["amount"])
                for payment in invoice.get("payments") or []
                if payment["paid_on"] <= day
            ),
            _ZERO,
        )
    )


def _amount_paid(invoice: dict[str, Any]) -> Decimal:
    """All payments recorded against the invoice."""
    paid = (money(payment["amount"]) for payment in invoice.get("payments") or [])
    return money(sum(paid, _ZERO))


def _outstanding(invoice: dict[str, Any]) -> Decimal:
    """Invoice total minus payments."""
    return money(money(invoice["total_amount"]) - _amount_paid(invoice))


def _is_open(invoice: dict[str, Any]) -> bool:
    """True when the invoice still asks the unit to pay."""
    if invoice.get("status") == "cancelled":
        return False
    return _outstanding(invoice) > 0


def _invoice_view(invoice: dict[str, Any], as_of: date) -> dict[str, Any]:
    """One open invoice in the balance response."""
    stored = invoice.get("status") or "issued"
    return {
        "id": str(invoice["id"]),
        "invoice_number": invoice.get("invoice_number"),
        "billing_month": _iso(invoice["billing_month"]),
        "invoice_date": _iso(invoice["invoice_date"]),
        "due_date": _iso(invoice["due_date"]),
        "status": invoice_status(stored, invoice["due_date"], as_of),
        "total_amount": money_str(invoice["total_amount"]),
        "amount_paid": money_str(_amount_paid(invoice)),
        "outstanding": money_str(_outstanding(invoice)),
        "lines": [
            {
                "line_role": line.get("line_role") or "charge",
                "kind": line.get("kind"),
                "description": line.get("description") or "",
                "line_total": money_str(line["line_total"]),
            }
            for line in invoice.get("lines") or []
        ],
    }


def _iso(value: date | str) -> str:
    """ISO date text."""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
