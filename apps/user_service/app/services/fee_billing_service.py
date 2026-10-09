"""Issue maintenance and club invoices for one run date."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.fee_billing_repository import (
    FeeBillingRepository,
    fetch_collection_rows,
    record_fee_invoice_activity,
)
from apps.user_service.app.schemas.enums.fee_configuration import (
    FeeBillingCycle,
    FeeFrequency,
    FeeHeadKind,
    FeeHeadStatus,
    FeeStartRule,
)
from apps.user_service.app.schemas.enums.property import PropertyType
from apps.user_service.app.services.fee_configuration_labels import (
    half_up_rupee,
    money,
    money_str,
    period_months,
)
from apps.user_service.app.services.fee_invoice_mail import invoice_unit_context
from apps.user_service.app.services.fee_late_fee import (
    billing_today,
    build_collection_summary,
    build_outstanding_summary,
    build_unit_balance,
    invoice_status,
    late_fee_drafts,
    plan_credit_applications,
)
from apps.user_service.app.services.units_service import resolve_unit_property_type
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("fee_billing_service")

ELECTRICITY_NOT_READY = "electricity_not_ready"
PRO_RATA_NOT_READY = "pro_rata_not_ready"
MISSING_AREA = "missing_area"
SKIP_REASONS = (ELECTRICITY_NOT_READY, PRO_RATA_NOT_READY, MISSING_AREA)
_SKIP_ELECTRICITY = "skip_electricity"
_SKIP_PRO_RATA = "skip_pro_rata"
_BILL = "bill"
_IGNORE = "ignore"
_AREA_FIELD = {
    PropertyType.RESIDENTIAL.value: "area_sqft",
    PropertyType.COMMERCIAL.value: "carpet_area_sqft",
    PropertyType.PLOTS.value: "size_sqft",
}


def _empty_skip_counts() -> dict[str, int]:
    """Zero count for each named skip reason."""
    return {reason: 0 for reason in SKIP_REASONS}


def _as_date(value: Any) -> date | None:
    """Accept a date or an ISO date string."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _start_reached(head: dict[str, Any], run_date: date) -> bool:
    """True when the head's start rule allows billing on the run date."""
    if head.get("fee_start_rule") != FeeStartRule.SPECIFIC_DATE.value:
        return True
    start = _as_date(head.get("fee_start_date"))
    if start is None:
        return False
    return run_date >= start


def _cycle_matches(head: dict[str, Any], run_date: date) -> bool:
    """True when this month is one of the head's billing months."""
    if head.get("frequency") == FeeFrequency.MONTHLY.value:
        return True
    anchor = head.get("cycle_anchor_month")
    if anchor is None:
        return False
    return run_date.month in period_months(str(head.get("frequency") or ""), int(anchor))


def _is_due_today(head: dict[str, Any], run_date: date) -> bool:
    """True when the head is active, starts by today, and its invoice day is today."""
    status = head.get("status")
    if status is not None and status != FeeHeadStatus.ACTIVE.value:
        return False
    if int(head["invoice_day"]) != run_date.day:
        return False
    return _start_reached(head, run_date)


def _head_action(head: dict[str, Any], run_date: date) -> str:
    """Decide whether a head is billed, skipped, or left for another day."""
    if not _is_due_today(head, run_date):
        return _IGNORE
    kind = head.get("kind")
    if kind == FeeHeadKind.ELECTRICITY.value:
        return _SKIP_ELECTRICITY if _cycle_matches(head, run_date) else _IGNORE
    if head.get("billing_cycle") == FeeBillingCycle.PRO_RATA.value:
        return _SKIP_PRO_RATA
    if not _cycle_matches(head, run_date):
        return _IGNORE
    if kind in {FeeHeadKind.MAINTENANCE.value, FeeHeadKind.CLUB.value}:
        return _BILL
    return _IGNORE


def _description(head: dict[str, Any]) -> str:
    """Line text snapshotted from the fee head."""
    raw = head.get("line_description") or head.get("name")
    return str(raw).strip() if raw else "Fee"


def _line_tax(taxable: Decimal, tax: dict[str, Any]) -> Decimal:
    """Tax on one line from the fee head's single rate."""
    if not isinstance(tax, dict) or not tax.get("applicable"):
        return Decimal("0.00")
    rate = tax.get("rate_percent")
    if rate is None:
        return Decimal("0.00")
    return money(taxable * Decimal(str(rate)) / Decimal("100"))


def _area(property_type: str | None, unit: dict[str, Any]) -> Decimal | None:
    """Area used for maintenance: apartment, carpet, or plot size."""
    field = _AREA_FIELD.get(property_type or "")
    if field is None or unit.get(field) is None:
        return None
    area = money(unit[field])
    if area <= 0:
        return None
    return area


def _enabled_scope(head: dict[str, Any], property_type: str | None) -> dict[str, Any] | None:
    """The enabled scope for this property type, if the head bills it."""
    if property_type is None:
        return None
    for scope in head.get("scopes") or []:
        if scope.get("property_type") == property_type and scope.get("enabled"):
            return scope
    return None


def _draft(
    head: dict[str, Any],
    *,
    taxable: Decimal,
    area_or_quantity: Decimal,
    rate: Decimal,
    minimum_amount: Decimal | None,
) -> dict[str, Any]:
    """One invoice line before it is attached to an invoice id."""
    tax_amount = _line_tax(taxable, head.get("tax") or {})
    return {
        "fee_head_id": str(head["id"]),
        "kind": head["kind"],
        "fee_head_version": int(head["version"]),
        "description": _description(head),
        "area_or_quantity": area_or_quantity,
        "rate": money(rate),
        "minimum_amount": None if minimum_amount is None else money(minimum_amount),
        "taxable_amount": money(taxable),
        "tax_amount": tax_amount,
        "line_total": money(taxable + tax_amount),
        "due_within_days": int(head["due_within_days"]),
    }


def _club_amount(head: dict[str, Any]) -> Decimal | None:
    """Flat club charge, or None when the head has no amount."""
    charge = head.get("charge") or {}
    if not isinstance(charge, dict) or charge.get("amount") is None:
        return None
    return money(charge["amount"])


def _maintenance_line(
    head: dict[str, Any], unit: dict[str, Any], scope: dict[str, Any], property_type: str
) -> tuple[str, dict[str, Any] | None]:
    """Maintenance line, or a missing-area skip."""
    area = _area(property_type, unit)
    if area is None:
        return "skip", {
            "reason": MISSING_AREA,
            "unit_id": str(unit["id"]),
            "fee_head_id": str(head["id"]),
        }
    rate = scope.get("rate_per_sqft")
    if rate is None:
        return _IGNORE, None
    minimum = scope.get("minimum_amount")
    floor = money(minimum) if minimum is not None else Decimal("0.00")
    taxable = max(money(Decimal(str(rate)) * area), floor)
    return "line", _draft(
        head,
        taxable=taxable,
        area_or_quantity=area,
        rate=money(rate),
        minimum_amount=floor,
    )


def _line_for_unit(head: dict[str, Any], unit: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """A line, a skip, or ignore for one head applied to one unit."""
    property_type = resolve_unit_property_type(unit)
    scope = _enabled_scope(head, property_type)
    if scope is None:
        return _IGNORE, None
    if head.get("kind") == FeeHeadKind.MAINTENANCE.value:
        return _maintenance_line(head, unit, scope, property_type or "")
    if head.get("kind") == FeeHeadKind.CLUB.value:
        amount = _club_amount(head)
        if amount is None:
            return _IGNORE, None
        return "line", _draft(
            head,
            taxable=amount,
            area_or_quantity=Decimal("1.00"),
            rate=amount,
            minimum_amount=None,
        )
    return _IGNORE, None


def _head_skip(head: dict[str, Any], reason: str) -> dict[str, Any]:
    """A head-level skip with no unit."""
    return {"reason": reason, "unit_id": None, "fee_head_id": str(head["id"])}


def _collect(
    work: list[tuple[dict[str, Any], str]], units: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[tuple[str, int], list[dict[str, Any]]]]:
    """Turn due heads into skip rows and lines grouped by unit and invoice day."""
    skips: list[dict[str, Any]] = []
    drafts: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for head, action in work:
        if action == _SKIP_ELECTRICITY:
            skips.append(_head_skip(head, ELECTRICITY_NOT_READY))
            continue
        if action == _SKIP_PRO_RATA:
            skips.append(_head_skip(head, PRO_RATA_NOT_READY))
            continue
        if action != _BILL:
            continue
        _collect_head_units(head, units, skips, drafts)
    return skips, drafts


def _collect_head_units(
    head: dict[str, Any],
    units: list[dict[str, Any]],
    skips: list[dict[str, Any]],
    drafts: dict[tuple[str, int], list[dict[str, Any]]],
) -> None:
    """Append lines or missing-area skips for one billable head."""
    invoice_day = int(head["invoice_day"])
    for unit in units:
        outcome, payload = _line_for_unit(head, unit)
        if outcome == "skip" and payload is not None:
            skips.append(payload)
        elif outcome == "line" and payload is not None:
            drafts[(str(unit["id"]), invoice_day)].append(payload)


def _invoice_amounts(lines: list[dict[str, Any]]) -> dict[str, Decimal]:
    """Taxable, tax, half-up total, and the paise round-off."""
    taxable = money(sum((line["taxable_amount"] for line in lines), Decimal("0")))
    tax = money(sum((line["tax_amount"] for line in lines), Decimal("0")))
    exact = money(taxable + tax)
    total = half_up_rupee(exact)
    return {
        "taxable_amount": taxable,
        "tax_amount": tax,
        "round_off_amount": money(total - exact),
        "total_amount": total,
    }


def _due_date(run_date: date, lines: list[dict[str, Any]]) -> date:
    """Run date plus the longest due-within-days among the merged lines."""
    days = max(int(line["due_within_days"]) for line in lines)
    return run_date + timedelta(days=days)


def _invoice_summary(row: dict[str, Any], as_of: date) -> dict[str, Any]:
    """One invoice header with the amount still outstanding."""
    total = money(row["total_amount"])
    paid = money(row["amount_paid"])
    return {
        "id": str(row["id"]),
        "invoice_number": row["invoice_number"],
        "unit_id": str(row["unit_id"]),
        "unit_code": row["unit_code"],
        "billing_month": _iso_date(row["billing_month"]),
        "invoice_date": _iso_date(row["invoice_date"]),
        "due_date": _iso_date(row["due_date"]),
        "status": invoice_status(row["status"], row["due_date"], as_of),
        "total_amount": money_str(total),
        "amount_paid": money_str(paid),
        "outstanding": money_str(_amount_still_due(row, total, paid)),
        "pdf_path": row.get("pdf_path"),
    }


def _optional_money(value: Decimal | str | int | float | None) -> str | None:
    """A money string, or nothing when the column is empty."""
    if value is None:
        return None
    return money_str(value)


def _invoice_line_detail(line: dict[str, Any]) -> dict[str, Any]:
    """One invoice line with its taxable amount and tax parts."""
    source = line.get("source_invoice_id")
    return {
        "id": str(line["id"]),
        "kind": line["kind"],
        "line_role": line.get("line_role") or "charge",
        "description": line["description"],
        "area_or_quantity": _optional_money(line.get("area_or_quantity")),
        "rate": _optional_money(line.get("rate")),
        "minimum_amount": _optional_money(line.get("minimum_amount")),
        "taxable_amount": money_str(line["taxable_amount"]),
        "tax_amount": money_str(line["tax_amount"]),
        "line_total": money_str(line["line_total"]),
        "source_invoice_id": str(source) if source else None,
        "started_months": line.get("started_months"),
        "days_overdue": line.get("days_overdue"),
    }


def _invoice_detail(row: dict[str, Any], as_of: date) -> dict[str, Any]:
    """Invoice header, lines, and payments."""
    total = money(row["total_amount"])
    paid = money(sum((money(item["amount"]) for item in row.get("payments") or []), Decimal("0")))
    return {
        "id": str(row["id"]),
        "invoice_number": row["invoice_number"],
        "unit_id": str(row["unit_id"]),
        "unit_code": row["unit_code"],
        "billing_month": _iso_date(row["billing_month"]),
        "invoice_date": _iso_date(row["invoice_date"]),
        "due_date": _iso_date(row["due_date"]),
        "status": invoice_status(row["status"], row["due_date"], as_of),
        "taxable_amount": money_str(row["taxable_amount"]),
        "tax_amount": money_str(row["tax_amount"]),
        "round_off_amount": money_str(row["round_off_amount"]),
        "total_amount": money_str(total),
        "amount_paid": money_str(paid),
        "outstanding": money_str(_amount_still_due(row, total, paid)),
        "pdf_path": row.get("pdf_path"),
        "lines": [_invoice_line_detail(line) for line in row.get("lines") or []],
        "payments": [
            {
                "amount": money_str(payment["amount"]),
                "paid_on": _iso_date(payment["paid_on"]),
                "mode": payment["mode"],
                "reference": payment.get("reference"),
            }
            for payment in row.get("payments") or []
        ],
        "activities": [_activity_detail(activity) for activity in row.get("activities") or []],
    }


def _activity_detail(activity: dict[str, Any]) -> dict[str, Any]:
    """One invoice activity for the detail response."""
    detail = activity.get("detail") or {}
    if isinstance(detail, str):
        detail = json.loads(detail)
    actor = activity.get("actor_user_id")
    return {
        "event": activity["event"],
        "actor_user_id": str(actor) if actor else None,
        "detail": detail,
        "created_at": _iso_timestamp(activity["created_at"]),
    }


def _iso_timestamp(value: datetime | str) -> str:
    """ISO timestamp text."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _reminder_notice(
    detail: dict[str, Any],
    *,
    organization_id: str,
    project_id: str,
    project_name: str,
    sent_at: str,
) -> dict[str, Any]:
    """Mail and push payload for one unpaid invoice."""
    return {
        "invoice_id": str(detail["id"]),
        "organization_id": organization_id,
        "project_id": project_id,
        "unit_id": str(detail["unit_id"]),
        "invoice_number": detail["invoice_number"],
        "unit_code": str(detail.get("unit_code") or "").strip(),
        "project_name": project_name,
        "pdf_path": detail.get("pdf_path"),
        "billing_month": _iso_date(detail["billing_month"]),
        "invoice_date": _iso_date(detail["invoice_date"]),
        "due_date": _iso_date(detail["due_date"]),
        "taxable_amount": money_str(detail["taxable_amount"]),
        "tax_amount": money_str(detail["tax_amount"]),
        "round_off_amount": money_str(detail["round_off_amount"]),
        "total_amount": money_str(detail["total_amount"]),
        "lines": [_reminder_line(line) for line in detail.get("lines") or []],
        "remind_on": sent_at,
    }


def _reminder_line(line: dict[str, Any]) -> dict[str, str | None]:
    """One invoice line as money text for the reminder PDF."""
    return {
        "description": str(line["description"]),
        "kind": str(line.get("kind") or ""),
        "area_or_quantity": _optional_money(line.get("area_or_quantity")),
        "rate": _optional_money(line.get("rate")),
        "taxable_amount": money_str(line["taxable_amount"]),
        "tax_amount": money_str(line["tax_amount"]),
        "line_total": money_str(line["line_total"]),
    }


def _amount_still_due(row: dict[str, Any], total: Decimal, paid: Decimal) -> Decimal:
    """Nothing is due once the invoice is cancelled."""
    if row.get("status") == "cancelled":
        return money(0)
    return money(total - paid)


def _remember_payment(invoice: dict[str, Any], amount: Decimal, paid_on: date) -> str:
    """Append a payment and return the invoice's new status."""
    invoice.setdefault("payments", []).append({"amount": amount, "paid_on": paid_on})
    paid = money(sum((money(row["amount"]) for row in invoice["payments"]), Decimal("0")))
    status = "paid" if paid >= money(invoice["total_amount"]) else "partial"
    invoice["status"] = status
    return status


def _iso_date(value: date | str) -> str:
    """Render a date as YYYY-MM-DD."""
    if isinstance(value, date):
        return value.isoformat()
    return value


def _line_row(
    line: dict[str, Any], *, invoice_id: str, organization_id: str, project_id: str
) -> dict[str, Any]:
    """Drop the scheduling field and attach the invoice identity."""
    return {
        "invoice_id": invoice_id,
        "organization_id": organization_id,
        "project_id": project_id,
        "fee_head_id": line["fee_head_id"],
        "kind": line["kind"],
        "fee_head_version": line["fee_head_version"],
        "description": line["description"],
        "area_or_quantity": line["area_or_quantity"],
        "rate": line["rate"],
        "minimum_amount": line["minimum_amount"],
        "taxable_amount": line["taxable_amount"],
        "tax_amount": line["tax_amount"],
        "line_total": line["line_total"],
        "line_role": line.get("line_role") or "charge",
        "source_invoice_id": line.get("source_invoice_id"),
        "source_line_id": line.get("source_line_id"),
        "started_months": line.get("started_months"),
        "days_overdue": line.get("days_overdue"),
    }


class FeeBillingService:
    """Issues maintenance and club invoices, and records anything it skips."""

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        repository: FeeBillingRepository | None = None,
    ) -> None:
        self.repo = repository or FeeBillingRepository(db_connection)
        self.mailer = None
        self.outbound: list[dict[str, Any]] = []
        self._notices: list[dict[str, Any]] = []

    async def issue_due(self, *, run_date: date) -> dict[str, Any]:
        """Bill every project that has a fee head due on this date."""
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for head in await self.repo.list_active_heads():
            key = (str(head["organization_id"]), str(head["project_id"]))
            grouped[key].append(head)
        prior_grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for invoice in await self.repo.list_collectible_invoices(before=run_date):
            key = (str(invoice["organization_id"]), str(invoice["project_id"]))
            prior_grouped[key].append(invoice)

        invoice_ids: list[str] = []
        skip_counts = _empty_skip_counts()
        project_keys = list(dict.fromkeys([*grouped.keys(), *prior_grouped.keys()]))
        for organization_id, project_id in project_keys:
            created, counts = await self._issue_project(
                organization_id=organization_id,
                project_id=project_id,
                heads=grouped.get((organization_id, project_id), []),
                priors=prior_grouped.get((organization_id, project_id), []),
                run_date=run_date,
            )
            invoice_ids.extend(created)
            for reason, count in counts.items():
                skip_counts[reason] += count
        await self._queue_invoice_notices()
        return {
            "run_date": run_date.isoformat(),
            "invoice_ids": invoice_ids,
            "skip_counts": skip_counts,
        }

    async def _issue_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        heads: list[dict[str, Any]],
        priors: list[dict[str, Any]],
        run_date: date,
    ) -> tuple[list[str], dict[str, int]]:
        """Create one project's invoices and skips for the run date."""
        await self._apply_credits(
            organization_id=organization_id,
            project_id=project_id,
            mirror=priors,
        )
        work = [
            (head, action) for head in heads if (action := _head_action(head, run_date)) != _IGNORE
        ]
        drafts: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        skips: list[dict[str, Any]] = []
        if work:
            units: list[dict[str, Any]] = []
            if any(action == _BILL for _, action in work):
                units = await self.repo.list_units(
                    organization_id=organization_id, project_id=project_id
                )
            skips, drafts = _collect(work, units)
        for unit_id, lines in late_fee_drafts(priors, run_date).items():
            drafts[(unit_id, run_date.day)].extend(lines)
        if not skips and not drafts:
            return [], _empty_skip_counts()
        created, counts = await self._persist(
            organization_id=organization_id,
            project_id=project_id,
            run_date=run_date,
            skips=skips,
            drafts=drafts,
        )
        await self._apply_credits(organization_id=organization_id, project_id=project_id)
        return created, counts

    async def _persist(
        self,
        *,
        organization_id: str,
        project_id: str,
        run_date: date,
        skips: list[dict[str, Any]],
        drafts: dict[tuple[str, int], list[dict[str, Any]]],
    ) -> tuple[list[str], dict[str, int]]:
        """Write the run, skips, and invoices. A repeat finds the invoice and stops."""
        run_id = await self.repo.ensure_run(
            organization_id=organization_id,
            project_id=project_id,
            run_date=run_date,
        )
        skip_counts = await self._insert_skips(
            skips,
            run_id=run_id,
            organization_id=organization_id,
            project_id=project_id,
        )
        invoice_ids = await self._insert_invoices(
            drafts,
            run_id=run_id,
            organization_id=organization_id,
            project_id=project_id,
            run_date=run_date,
        )
        await self.repo.add_run_counts(
            run_id=run_id,
            invoices_created=len(invoice_ids),
            lines_skipped=sum(skip_counts.values()),
        )
        return invoice_ids, skip_counts

    async def _insert_skips(
        self,
        skips: list[dict[str, Any]],
        *,
        run_id: str,
        organization_id: str,
        project_id: str,
    ) -> dict[str, int]:
        """Insert skip rows and count the ones that were new."""
        counts = _empty_skip_counts()
        for skip in skips:
            inserted = await self.repo.insert_skip(
                {
                    "run_id": run_id,
                    "organization_id": organization_id,
                    "project_id": project_id,
                    "unit_id": skip.get("unit_id"),
                    "fee_head_id": skip.get("fee_head_id"),
                    "reason": skip["reason"],
                }
            )
            if inserted:
                counts[skip["reason"]] += 1
        return counts

    async def _insert_invoices(
        self,
        drafts: dict[tuple[str, int], list[dict[str, Any]]],
        *,
        run_id: str,
        organization_id: str,
        project_id: str,
        run_date: date,
    ) -> list[str]:
        """Insert invoices that do not already exist for the unit, day, and month."""
        invoice_ids: list[str] = []
        billing_month = run_date.replace(day=1)
        for (unit_id, invoice_day), lines in drafts.items():
            invoice_id = await self._insert_one_invoice(
                lines,
                run_id=run_id,
                organization_id=organization_id,
                project_id=project_id,
                unit_id=unit_id,
                invoice_day=invoice_day,
                billing_month=billing_month,
                run_date=run_date,
            )
            if invoice_id is not None:
                invoice_ids.append(invoice_id)
        return invoice_ids

    async def _insert_one_invoice(
        self,
        lines: list[dict[str, Any]],
        *,
        run_id: str,
        organization_id: str,
        project_id: str,
        unit_id: str,
        invoice_day: int,
        billing_month: date,
        run_date: date,
    ) -> str | None:
        """Insert one merged invoice and its lines, or return None when it exists."""
        existing = await self.repo.find_invoice(
            project_id=project_id,
            unit_id=unit_id,
            invoice_day=invoice_day,
            billing_month=billing_month,
        )
        if existing is not None:
            return None
        amounts = _invoice_amounts(lines)
        due_on = _due_date(run_date, lines)
        invoice = {
            "organization_id": organization_id,
            "project_id": project_id,
            "unit_id": unit_id,
            "run_id": run_id,
            "invoice_day": invoice_day,
            "billing_month": billing_month,
            "invoice_date": run_date,
            "due_date": due_on,
            "status": "issued",
            **amounts,
        }
        invoice_id = await self.repo.insert_invoice(invoice)
        if invoice_id is None:
            return None
        for line in lines:
            await self.repo.insert_line(
                _line_row(
                    line,
                    invoice_id=invoice_id,
                    organization_id=organization_id,
                    project_id=project_id,
                )
            )
        await self._record_activity(
            organization_id=organization_id,
            project_id=project_id,
            invoice_id=invoice_id,
            event="issued",
            detail={
                "invoice_number": invoice.get("invoice_number"),
                "total_amount": money_str(invoice["total_amount"]),
            },
        )
        await self._email_new_invoice(invoice, lines=lines, invoice_id=invoice_id)
        return invoice_id

    async def _email_new_invoice(
        self,
        invoice: dict[str, Any],
        *,
        lines: list[dict[str, Any]],
        invoice_id: str,
    ) -> None:
        """Email the new invoice. A mail problem does not undo the invoice."""
        notice = {
            "invoice_id": invoice_id,
            "organization_id": invoice["organization_id"],
            "project_id": invoice["project_id"],
            "unit_id": invoice["unit_id"],
            "invoice_number": invoice["invoice_number"],
            "unit_code": invoice.get("unit_code") or "",
            "billing_month": _iso_date(invoice["billing_month"]),
            "invoice_date": _iso_date(invoice["invoice_date"]),
            "due_date": _iso_date(invoice["due_date"]),
            "taxable_amount": money_str(invoice["taxable_amount"]),
            "tax_amount": money_str(invoice["tax_amount"]),
            "round_off_amount": money_str(invoice["round_off_amount"]),
            "total_amount": money_str(invoice["total_amount"]),
            "lines": [
                {
                    "description": line["description"],
                    "kind": line.get("kind") or "",
                    "area_or_quantity": _optional_money(line.get("area_or_quantity")),
                    "rate": _optional_money(line.get("rate")),
                    "taxable_amount": money_str(line["taxable_amount"]),
                    "tax_amount": money_str(line["tax_amount"]),
                    "line_total": money_str(line["line_total"]),
                }
                for line in lines
            ],
        }
        self._notices.append(notice)

    async def _queue_invoice_notices(self) -> None:
        """Hand new invoices to the mailer after every project has been billed."""
        notices = self._notices
        self._notices = []
        for notice in notices:
            try:
                if self.mailer is not None:
                    await self.mailer(notice)
                else:
                    self.outbound.append(notice)
            except Exception:
                logger.exception(
                    "fee invoice email failed invoice_number=%s",
                    notice.get("invoice_number"),
                )

    async def _apply_credits(
        self,
        *,
        organization_id: str,
        project_id: str,
        mirror: list[dict[str, Any]] | None = None,
    ) -> None:
        """Apply unapplied credit to open invoices, oldest receipt and invoice first."""
        credit_rows = await self.repo.list_open_credits(
            organization_id=organization_id,
            project_id=project_id,
        )
        if not credit_rows:
            return
        invoices = await self.repo.list_open_invoices(
            organization_id=organization_id,
            project_id=project_id,
        )
        credits_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
        invoices_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for credit in credit_rows:
            credits_by_unit[str(credit["unit_id"])].append(credit)
        for invoice in invoices:
            invoices_by_unit[str(invoice["unit_id"])].append(invoice)
        mirror_by_id = {str(row["id"]): row for row in mirror or []}
        for unit_id, unit_credits in credits_by_unit.items():
            applications = plan_credit_applications(
                invoices_by_unit.get(unit_id, []),
                unit_credits,
            )
            await self._store_credit_applications(
                applications,
                invoices_by_unit.get(unit_id, []),
                mirror_by_id,
            )

    async def _store_credit_applications(
        self,
        applications: list[dict[str, Any]],
        invoices: list[dict[str, Any]],
        mirror_by_id: dict[str, dict[str, Any]],
    ) -> None:
        """Persist credit applications and reflect them on the in-memory invoices."""
        invoices_by_id = {str(invoice["id"]): invoice for invoice in invoices}
        for application in applications:
            await self.repo.insert_payment(
                {
                    "organization_id": application["organization_id"],
                    "project_id": application["project_id"],
                    "invoice_id": application["invoice_id"],
                    "amount": application["amount"],
                    "paid_on": application["paid_on"],
                    "mode": application["mode"],
                    "reference": application["reference"],
                }
            )
            await self.repo.consume_credit(
                credit_id=application["credit_id"],
                amount=application["amount"],
            )
            target = invoices_by_id[application["invoice_id"]]
            status = _remember_payment(target, application["amount"], application["paid_on"])
            reflected = mirror_by_id.get(application["invoice_id"])
            if reflected is not None and reflected is not target:
                status = _remember_payment(reflected, application["amount"], application["paid_on"])
            await self.repo.update_invoice_status(
                invoice_id=application["invoice_id"],
                status=status,
            )
            await self._record_activity(
                organization_id=str(application["organization_id"]),
                project_id=str(application["project_id"]),
                invoice_id=str(application["invoice_id"]),
                event="payment_recorded",
                detail={
                    "amount": money_str(application["amount"]),
                    "paid_on": _iso_date(application["paid_on"]),
                    "mode": application["mode"],
                    "reference": application.get("reference"),
                    "source": "credit",
                    "status": status,
                },
            )

    async def record_reminder(
        self,
        *,
        organization_id: str,
        project_id: str,
        invoice_id: str,
        actor_user_id: str,
        email_count: int,
        source: str = "manual",
    ) -> None:
        """Record a reminder that was queued for this invoice."""
        await self._record_activity(
            organization_id=organization_id,
            project_id=project_id,
            invoice_id=invoice_id,
            event="reminder_sent",
            actor_user_id=actor_user_id,
            detail={"source": source, "email_count": email_count},
        )

    async def _record_activity(
        self,
        *,
        organization_id: str,
        project_id: str,
        invoice_id: str,
        event: str,
        actor_user_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Store one activity. Tests without a connection keep it on the fake."""
        connection = getattr(self.repo, "db_connection", None)
        if connection is None:
            self.repo.activities.append(
                {
                    "organization_id": organization_id,
                    "project_id": project_id,
                    "invoice_id": invoice_id,
                    "event": event,
                    "actor_user_id": actor_user_id,
                    "detail": detail or {},
                }
            )
            return
        await record_fee_invoice_activity(
            connection,
            organization_id=organization_id,
            project_id=project_id,
            invoice_id=invoice_id,
            event=event,
            actor_user_id=actor_user_id,
            detail=detail,
        )

    async def record_payment(
        self,
        *,
        organization_id: str,
        project_id: str,
        invoice_id: str,
        amount: Decimal,
        paid_on: date,
        mode: str,
        reference: str | None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Record a payment and update the invoice to partial or paid."""
        invoice = await self.repo.get_invoice(
            organization_id=organization_id,
            project_id=project_id,
            invoice_id=invoice_id,
        )
        if invoice is None:
            raise NotFoundException(message_key="fee_billing.errors.invoice_not_found")
        if invoice.get("status") == "cancelled":
            raise ConflictException(
                message_key="fee_billing.errors.invoice_cancelled",
                custom_code=CustomStatusCode.CONFLICT,
            )
        paid = money(amount)
        if paid <= 0:
            raise ValidationException(message_key="fee_billing.errors.invalid_payment")
        payments = await self.repo.list_payments(invoice_id=invoice_id)
        already = money(sum((money(row["amount"]) for row in payments), Decimal("0")))
        outstanding = money(money(invoice["total_amount"]) - already)
        applied = money(0) if outstanding <= 0 else min(paid, outstanding)
        surplus = money(paid - applied)
        if applied > 0:
            await self.repo.insert_payment(
                {
                    "organization_id": organization_id,
                    "project_id": project_id,
                    "invoice_id": invoice_id,
                    "amount": applied,
                    "paid_on": paid_on,
                    "mode": mode,
                    "reference": reference,
                }
            )
            status = _remember_payment(invoice, applied, paid_on)
            await self.repo.update_invoice_status(invoice_id=invoice_id, status=status)
            await self._record_activity(
                organization_id=organization_id,
                project_id=project_id,
                invoice_id=invoice_id,
                event="payment_recorded",
                actor_user_id=actor_user_id,
                detail={
                    "amount": money_str(applied),
                    "paid_on": paid_on.isoformat(),
                    "mode": mode,
                    "reference": reference,
                    "status": status,
                },
            )
        else:
            status = str(invoice.get("status") or "paid")
        if surplus > 0:
            await self.repo.insert_credit(
                {
                    "organization_id": organization_id,
                    "project_id": project_id,
                    "unit_id": str(invoice["unit_id"]),
                    "source_invoice_id": invoice_id,
                    "amount": surplus,
                    "paid_on": paid_on,
                    "mode": mode,
                    "reference": reference,
                }
            )
        await self._apply_credits(organization_id=organization_id, project_id=project_id)
        amount_paid = money(already + applied)
        credit = await self.repo.unit_credit(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=str(invoice["unit_id"]),
        )
        return {
            "invoice_id": invoice_id,
            "status": status,
            "amount": money_str(paid),
            "paid_on": paid_on.isoformat(),
            "mode": mode,
            "reference": reference,
            "amount_paid": money_str(amount_paid),
            "outstanding": money_str(money(invoice["total_amount"]) - amount_paid),
            "credit": money_str(credit),
        }

    async def list_invoices(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str | None,
        unit_ids: list[str] | None = None,
        status: str | None,
        billing_months: list[date] | None,
        page: int,
        page_size: int,
        as_of: date | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List project invoices, optionally limited to a unit, status, or months."""
        if unit_ids is not None and not unit_ids:
            return [], 0
        as_of = billing_today() if as_of is None else as_of
        months = sorted({month.replace(day=1) for month in (billing_months or [])})
        rows, total = await self.repo.list_project_invoices(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
            unit_ids=unit_ids,
            status=status,
            billing_months=months,
            as_of=as_of,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return [_invoice_summary(row, as_of) for row in rows], total

    async def collection_summary(
        self,
        *,
        organization_id: str,
        project_id: str,
        billing_months: list[date] | None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """Project cards for invoiced, collected, outstanding, and overdue."""
        as_of = billing_today() if as_of is None else as_of
        months = sorted({month.replace(day=1) for month in (billing_months or [])})
        connection = getattr(self.repo, "db_connection", None)
        if connection is None:
            rows = [
                row
                for row in getattr(self.repo, "project_invoices", [])
                if row.get("organization_id") == organization_id
                and row.get("project_id") == project_id
                and (not months or row.get("billing_month") in months)
            ]
        else:
            rows = await fetch_collection_rows(
                connection,
                organization_id=organization_id,
                project_id=project_id,
                billing_months=months,
            )
        summary = build_collection_summary(rows, as_of=as_of)
        summary["billing_months"] = [month.isoformat() for month in months]
        return summary

    async def invoice_detail(
        self,
        *,
        organization_id: str,
        project_id: str,
        invoice_id: str,
        unit_id: str | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """One invoice with lines and payments, or not found."""
        if unit_id is None:
            header = await self.repo.get_invoice(
                organization_id=organization_id,
                project_id=project_id,
                invoice_id=invoice_id,
            )
            if header is None:
                raise NotFoundException(message_key="fee_billing.errors.invoice_not_found")
            unit_id = str(header["unit_id"])
        row = await self.repo.get_invoice(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
            invoice_id=invoice_id,
        )
        if row is None:
            raise NotFoundException(message_key="fee_billing.errors.invoice_not_found")
        return _invoice_detail(row, billing_today() if as_of is None else as_of)

    async def cancel_invoice(
        self,
        *,
        organization_id: str,
        project_id: str,
        invoice_id: str,
        as_of: date | None = None,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an issued invoice that has not received a payment."""
        invoice = await self.repo.get_invoice(
            organization_id=organization_id,
            project_id=project_id,
            invoice_id=invoice_id,
        )
        if invoice is None:
            raise NotFoundException(message_key="fee_billing.errors.invoice_not_found")
        payments = await self.repo.list_payments(invoice_id=invoice_id)
        received = money(sum((money(row["amount"]) for row in payments), Decimal("0")))
        if invoice.get("status") != "issued" or received > 0:
            raise ConflictException(
                message_key="fee_billing.errors.invoice_not_cancellable",
                custom_code=CustomStatusCode.CONFLICT,
            )
        await self.repo.update_invoice_status(invoice_id=invoice_id, status="cancelled")
        await self._record_activity(
            organization_id=organization_id,
            project_id=project_id,
            invoice_id=invoice_id,
            event="cancelled",
            actor_user_id=actor_user_id,
            detail={"invoice_number": invoice.get("invoice_number")},
        )
        return await self.invoice_detail(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=str(invoice["unit_id"]),
            invoice_id=invoice_id,
            as_of=as_of,
        )

    async def reminder_notice(
        self,
        *,
        organization_id: str,
        project_id: str,
        invoice_id: str,
    ) -> dict[str, Any]:
        """Notice for a manual reminder. Paid and cancelled invoices are refused."""
        invoice = await self.repo.get_invoice(
            organization_id=organization_id,
            project_id=project_id,
            invoice_id=invoice_id,
        )
        if invoice is None:
            raise NotFoundException(message_key="fee_billing.errors.invoice_not_found")
        payments = await self.repo.list_payments(invoice_id=invoice_id)
        received = money(sum((money(row["amount"]) for row in payments), Decimal("0")))
        outstanding = money(money(invoice["total_amount"]) - received)
        if invoice.get("status") not in {"issued", "partial"} or outstanding <= 0:
            raise ConflictException(
                message_key="fee_billing.errors.invoice_not_remindable",
                custom_code=CustomStatusCode.CONFLICT,
            )
        detail = await self.repo.get_invoice(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=str(invoice["unit_id"]),
            invoice_id=invoice_id,
        )
        if detail is None:
            raise NotFoundException(message_key="fee_billing.errors.invoice_not_found")
        project_name = str(detail.get("project_name") or "")
        connection = getattr(self.repo, "db_connection", None)
        if connection is not None:
            place = await invoice_unit_context(
                connection,
                project_id=project_id,
                unit_id=str(invoice["unit_id"]),
            )
            project_name = place["project_name"]
        sent_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return _reminder_notice(
            detail,
            organization_id=organization_id,
            project_id=project_id,
            project_name=project_name,
            sent_at=sent_at,
        )

    async def unit_balance(
        self, *, organization_id: str, project_id: str, unit_id: str
    ) -> dict[str, Any]:
        """Open invoices for a unit, split into arrears, late fee, and current charges."""
        invoices = await self.repo.list_unit_invoices(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        credit = await self.repo.unit_credit(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        return build_unit_balance(invoices, credit=credit)

    async def outstanding_summary(
        self, *, organization_id: str, project_id: str, unit_id: str
    ) -> dict[str, Any]:
        """Total outstanding for the resident pending-payments card."""
        invoices = await self.repo.list_unit_invoices(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        credit = await self.repo.unit_credit(
            organization_id=organization_id,
            project_id=project_id,
            unit_id=unit_id,
        )
        return build_outstanding_summary(invoices, credit=credit)
