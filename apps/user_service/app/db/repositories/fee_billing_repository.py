"""Persistence for the daily fee invoice run."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_JSONB_COLUMNS = frozenset({"charge", "tax"})

_HEAD_SELECT = """
    SELECT
        id,
        organization_id,
        project_id,
        kind::text AS kind,
        name,
        line_description,
        status::text AS status,
        frequency::text AS frequency,
        billing_cycle::text AS billing_cycle,
        cycle_anchor_month,
        fee_start_rule::text AS fee_start_rule,
        fee_start_date,
        due_within_days,
        invoice_day,
        charge,
        tax,
        version
    FROM fee_heads
"""


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    """Decode jsonb columns that asyncpg returns as text."""
    for column in _JSONB_COLUMNS:
        value = row.get(column)
        if isinstance(value, str):
            row[column] = json.loads(value)
    return row


def _json_object(value: Any) -> dict[str, Any]:
    """Decode a jsonb object, defaulting to no late fee."""
    if isinstance(value, str):
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {"mode": "none"}
    if isinstance(value, dict):
        return value
    return {"mode": "none"}


class FeeBillingRepository(BaseRepository):
    """SQL for due fee heads, units, invoices, and skip rows."""

    async def list_active_heads(self) -> list[dict[str, Any]]:
        """Return every active fee head with its property scopes."""
        rows = await self.db_connection.fetch(
            f"""
            {_HEAD_SELECT}
            WHERE status = 'active'::fee_head_status
            ORDER BY organization_id, project_id, kind
            """
        )
        heads = [_decode_row(dict(row)) for row in rows]
        if not heads:
            return []
        scopes = await self._list_scopes([str(head["id"]) for head in heads])
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for scope in scopes:
            grouped[str(scope["fee_head_id"])].append(scope)
        for head in heads:
            head["scopes"] = grouped.get(str(head["id"]), [])
        return heads

    async def _list_scopes(self, fee_head_ids: list[str]) -> list[dict[str, Any]]:
        """Return property scopes for the given fee heads."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                fee_head_id,
                property_type::text AS property_type,
                enabled,
                rate_per_sqft,
                minimum_amount
            FROM fee_head_property_scopes
            WHERE fee_head_id = ANY($1::uuid[])
            """,
            fee_head_ids,
        )
        return [dict(row) for row in rows]

    async def list_units(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        """Return non-parking units that have an owner, tenant, or family member."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                u.id,
                u.plot_item_id,
                uc.config_kind::text AS config_kind,
                t.tower_type::text AS tower_type,
                uc.area_sqft,
                uc.carpet_area_sqft,
                pci.size_sqft
            FROM units u
            LEFT JOIN unit_configs uc ON uc.id = u.config_id
            LEFT JOIN towers t ON t.id = u.tower_id
            LEFT JOIN plot_config_items pci ON pci.id = u.plot_item_id
            WHERE u.organization_id = $1::uuid
              AND u.project_id = $2::uuid
              AND u.is_parking = false
              AND EXISTS (
                  SELECT 1
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
                  WHERE cu.organization_id = u.organization_id
                    AND cu.unit_id = u.id
                    AND cu.status IN (
                        'active'::contact_unit_status,
                        'pending'::contact_unit_status
                    )
                    AND c.status = 'active'
              )
            ORDER BY u.sort_order, u.code
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def ensure_run(self, *, organization_id: str, project_id: str, run_date: date) -> str:
        """Insert the project's run row for this date, or return the existing id."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO fee_billing_runs (organization_id, project_id, run_date)
            VALUES ($1::uuid, $2::uuid, $3::date)
            ON CONFLICT (project_id, run_date) DO NOTHING
            RETURNING id
            """,
            organization_id,
            project_id,
            run_date,
        )
        if row is not None:
            return str(row["id"])
        existing = await self.db_connection.fetchrow(
            """
            SELECT id
            FROM fee_billing_runs
            WHERE project_id = $1::uuid
              AND run_date = $2::date
            """,
            project_id,
            run_date,
        )
        return str(existing["id"])

    async def insert_skip(self, skip: dict[str, Any]) -> bool:
        """Insert a skip row. A repeat of the same reason is ignored."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO fee_billing_run_skips (
                run_id,
                organization_id,
                project_id,
                unit_id,
                fee_head_id,
                reason
            )
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            skip["run_id"],
            skip["organization_id"],
            skip["project_id"],
            skip.get("unit_id"),
            skip.get("fee_head_id"),
            skip["reason"],
        )
        return row is not None

    async def find_invoice(
        self,
        *,
        project_id: str,
        unit_id: str,
        invoice_day: int,
        billing_month: date,
    ) -> str | None:
        """Return the invoice id when this unit was already billed for the day and month."""
        row = await self.db_connection.fetchrow(
            """
            SELECT id
            FROM fee_invoices
            WHERE project_id = $1::uuid
              AND unit_id = $2::uuid
              AND invoice_day = $3
              AND billing_month = $4::date
            """,
            project_id,
            unit_id,
            invoice_day,
            billing_month,
        )
        if row is None:
            return None
        return str(row["id"])

    async def insert_invoice(self, invoice: dict[str, Any]) -> str | None:
        """Insert an invoice. A conflicting unit, day, and month returns None."""
        unit_code, number = await self._invoice_number(
            unit_id=invoice["unit_id"],
            invoice_date=invoice["invoice_date"],
        )
        invoice["unit_code"] = unit_code
        invoice["invoice_number"] = number
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO fee_invoices (
                organization_id,
                project_id,
                unit_id,
                run_id,
                invoice_day,
                billing_month,
                invoice_date,
                due_date,
                taxable_amount,
                tax_amount,
                round_off_amount,
                total_amount,
                status,
                invoice_number
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6::date, $7::date, $8::date,
                $9, $10, $11, $12, $13, $14
            )
            ON CONFLICT (project_id, unit_id, invoice_day, billing_month) DO NOTHING
            RETURNING id
            """,
            invoice["organization_id"],
            invoice["project_id"],
            invoice["unit_id"],
            invoice["run_id"],
            invoice["invoice_day"],
            invoice["billing_month"],
            invoice["invoice_date"],
            invoice["due_date"],
            invoice["taxable_amount"],
            invoice["tax_amount"],
            invoice["round_off_amount"],
            invoice["total_amount"],
            invoice["status"],
            invoice["invoice_number"],
        )
        if row is None:
            return None
        return str(row["id"])

    async def _invoice_number(self, *, unit_id: str, invoice_date: date) -> tuple[str, str]:
        """Unit code and INV-code-date, such as INV-LUX-B2101-20261001."""
        row = await self.db_connection.fetchrow(
            "SELECT code FROM units WHERE id = $1::uuid",
            unit_id,
        )
        code = str(row["code"]).strip()
        return code, f"INV-{code}-{invoice_date:%Y%m%d}"

    async def insert_line(self, line: dict[str, Any]) -> None:
        """Insert one snapshotted invoice line."""
        await self.db_connection.execute(
            """
            INSERT INTO fee_invoice_lines (
                invoice_id,
                organization_id,
                project_id,
                fee_head_id,
                kind,
                fee_head_version,
                description,
                area_or_quantity,
                rate,
                minimum_amount,
                taxable_amount,
                tax_amount,
                line_total,
                line_role,
                source_invoice_id,
                source_line_id,
                started_months,
                days_overdue
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::fee_head_kind, $6, $7,
                $8, $9, $10, $11, $12, $13, $14, $15::uuid, $16::uuid, $17, $18
            )
            """,
            line["invoice_id"],
            line["organization_id"],
            line["project_id"],
            line["fee_head_id"],
            line["kind"],
            line["fee_head_version"],
            line["description"],
            line["area_or_quantity"],
            line["rate"],
            line["minimum_amount"],
            line["taxable_amount"],
            line["tax_amount"],
            line["line_total"],
            line.get("line_role") or "charge",
            line.get("source_invoice_id"),
            line.get("source_line_id"),
            line.get("started_months"),
            line.get("days_overdue"),
        )

    async def add_run_counts(
        self, *, run_id: str, invoices_created: int, lines_skipped: int
    ) -> None:
        """Add this call's inserts to the run's counts."""
        await self.db_connection.execute(
            """
            UPDATE fee_billing_runs
            SET invoices_created = invoices_created + $2,
                lines_skipped = lines_skipped + $3,
                updated_at = now()
            WHERE id = $1::uuid
            """,
            run_id,
            invoices_created,
            lines_skipped,
        )

    async def list_collectible_invoices(self, *, before: date) -> list[dict[str, Any]]:
        """Earlier invoices past their due date, with lines, payments, and posted late fees."""
        invoices = await self._invoice_rows(
            """
            WHERE due_date < $1::date
              AND invoice_date < $1::date
            ORDER BY billing_month, invoice_date
            """,
            before,
        )
        return await self._with_children(invoices, include_late_fee_rule=True)

    async def list_unit_invoices(
        self, *, organization_id: str, project_id: str, unit_id: str
    ) -> list[dict[str, Any]]:
        """Every invoice for one unit, with lines and payments."""
        invoices = await self._invoice_rows(
            """
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND unit_id = $3::uuid
            ORDER BY billing_month, invoice_date
            """,
            organization_id,
            project_id,
            unit_id,
        )
        return await self._with_children(invoices, include_late_fee_rule=False)

    async def list_project_invoices(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str | None,
        unit_ids: list[str] | None,
        status: str | None,
        billing_months: list[date],
        as_of: date,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Invoice headers for a project, newest billing month first."""
        filters = [
            "i.organization_id = $1::uuid",
            "i.project_id = $2::uuid",
            "$3::date IS NOT NULL",
        ]
        args: list[Any] = [organization_id, project_id, as_of]
        if unit_ids is not None:
            args.append(unit_ids)
            filters.append(f"i.unit_id = ANY(${len(args)}::uuid[])")
        elif unit_id is not None:
            args.append(unit_id)
            filters.append(f"i.unit_id = ${len(args)}::uuid")
        if status == "overdue":
            filters.append("i.status IN ('issued', 'partial') AND i.due_date < $3::date")
        elif status == "paid":
            filters.append("i.status = 'paid'")
        elif status is not None:
            args.append(status)
            filters.append(f"i.status = ${len(args)} AND i.due_date >= $3::date")
        if billing_months:
            args.append(billing_months)
            filters.append(f"i.billing_month = ANY(${len(args)}::date[])")
        where_sql = " AND ".join(filters)
        count_row = await self.db_connection.fetchrow(
            f"""
            SELECT COUNT(*) AS total
            FROM fee_invoices i
            WHERE {where_sql}
            """,
            *args,
        )
        limit_index = len(args) + 1
        offset_index = len(args) + 2
        rows = await self.db_connection.fetch(
            f"""
            SELECT
                i.id,
                i.invoice_number,
                i.unit_id,
                u.code AS unit_code,
                i.billing_month,
                i.invoice_date,
                i.due_date,
                CASE
                    WHEN i.status <> 'paid' AND i.due_date < $3::date THEN 'overdue'
                    ELSE i.status
                END AS status,
                i.total_amount,
                COALESCE(paid.amount_paid, 0) AS amount_paid,
                i.pdf_path
            FROM fee_invoices i
            JOIN units u ON u.id = i.unit_id
            LEFT JOIN (
                SELECT invoice_id, SUM(amount) AS amount_paid
                FROM fee_invoice_payments
                GROUP BY invoice_id
            ) paid ON paid.invoice_id = i.id
            WHERE {where_sql}
            ORDER BY i.billing_month DESC, u.sort_order, u.code, i.invoice_date DESC
            LIMIT ${limit_index} OFFSET ${offset_index}
            """,
            *args,
            limit,
            offset,
        )
        total = int(count_row["total"]) if count_row is not None else 0
        return [dict(row) for row in rows], total

    async def get_invoice(
        self,
        *,
        organization_id: str,
        project_id: str,
        invoice_id: str,
        unit_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return one invoice, with lines when a unit is given."""
        if unit_id is None:
            return await self._invoice_header(
                organization_id=organization_id,
                project_id=project_id,
                invoice_id=invoice_id,
            )
        row = await self.db_connection.fetchrow(
            """
            SELECT
                i.id,
                i.invoice_number,
                i.unit_id,
                u.code AS unit_code,
                i.billing_month,
                i.invoice_date,
                i.due_date,
                i.status,
                i.taxable_amount,
                i.tax_amount,
                i.round_off_amount,
                i.total_amount,
                i.pdf_path
            FROM fee_invoices i
            JOIN units u ON u.id = i.unit_id
            WHERE i.id = $1::uuid
              AND i.organization_id = $2::uuid
              AND i.project_id = $3::uuid
              AND i.unit_id = $4::uuid
            """,
            invoice_id,
            organization_id,
            project_id,
            unit_id,
        )
        if row is None:
            return None
        invoice = dict(row)
        invoice["lines"] = await self._detail_lines(invoice_id)
        invoice["payments"] = await self._detail_payments(invoice_id)
        return invoice

    async def _invoice_header(
        self, *, organization_id: str, project_id: str, invoice_id: str
    ) -> dict[str, Any] | None:
        """Invoice columns used when recording a payment."""
        row = await self.db_connection.fetchrow(
            """
            SELECT id, organization_id, project_id, unit_id, total_amount, status
            FROM fee_invoices
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
              AND project_id = $3::uuid
            """,
            invoice_id,
            organization_id,
            project_id,
        )
        if row is None:
            return None
        return dict(row)

    async def _detail_lines(self, invoice_id: str) -> list[dict[str, Any]]:
        """Charge and late-fee lines for one invoice."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                id,
                kind::text AS kind,
                line_role,
                description,
                area_or_quantity,
                rate,
                minimum_amount,
                taxable_amount,
                tax_amount,
                line_total,
                source_invoice_id,
                started_months,
                days_overdue
            FROM fee_invoice_lines
            WHERE invoice_id = $1::uuid
            ORDER BY created_at, id
            """,
            invoice_id,
        )
        return [dict(row) for row in rows]

    async def _detail_payments(self, invoice_id: str) -> list[dict[str, Any]]:
        """Receipts applied to one invoice."""
        rows = await self.db_connection.fetch(
            """
            SELECT amount, paid_on, mode, reference
            FROM fee_invoice_payments
            WHERE invoice_id = $1::uuid
            ORDER BY paid_on, created_at
            """,
            invoice_id,
        )
        return [dict(row) for row in rows]

    async def list_open_invoices(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """Invoices in the project that still have a balance, oldest first."""
        invoices = await self._invoice_rows(
            """
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND status <> 'paid'
            ORDER BY billing_month, invoice_date, id
            """,
            organization_id,
            project_id,
        )
        return await self._with_children(invoices, include_late_fee_rule=False)

    async def list_open_credits(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """Unapplied credit for a project, oldest receipt first."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                id,
                unit_id,
                source_invoice_id,
                amount,
                paid_on,
                mode,
                reference
            FROM fee_unit_credits
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            ORDER BY paid_on, created_at, id
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def unit_credit(self, *, organization_id: str, project_id: str, unit_id: str) -> Decimal:
        """Unapplied credit still held for one unit."""
        row = await self.db_connection.fetchrow(
            """
            SELECT COALESCE(SUM(amount), 0) AS amount
            FROM fee_unit_credits
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND unit_id = $3::uuid
            """,
            organization_id,
            project_id,
            unit_id,
        )
        return Decimal(str(row["amount"])) if row is not None else Decimal("0")

    async def insert_credit(self, credit: dict[str, Any]) -> str:
        """Store surplus from a receipt and return its id."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO fee_unit_credits (
                organization_id, project_id, unit_id, source_invoice_id,
                amount, paid_on, mode, reference
            )
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6::date, $7, $8)
            RETURNING id
            """,
            credit["organization_id"],
            credit["project_id"],
            credit["unit_id"],
            credit["source_invoice_id"],
            credit["amount"],
            credit["paid_on"],
            credit["mode"],
            credit.get("reference"),
        )
        return str(row["id"])

    async def consume_credit(self, *, credit_id: str, amount: Decimal) -> None:
        """Reduce a credit row, and remove it once nothing remains."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE fee_unit_credits
            SET amount = amount - $2
            WHERE id = $1::uuid
              AND amount >= $2
            RETURNING amount
            """,
            credit_id,
            amount,
        )
        if row is not None and Decimal(str(row["amount"])) <= 0:
            await self.db_connection.execute(
                "DELETE FROM fee_unit_credits WHERE id = $1::uuid",
                credit_id,
            )

    async def list_payments(self, *, invoice_id: str) -> list[dict[str, Any]]:
        """Payments recorded against one invoice."""
        rows = await self.db_connection.fetch(
            """
            SELECT amount, paid_on
            FROM fee_invoice_payments
            WHERE invoice_id = $1::uuid
            ORDER BY paid_on, created_at
            """,
            invoice_id,
        )
        return [dict(row) for row in rows]

    async def insert_payment(self, payment: dict[str, Any]) -> str:
        """Insert one payment and return its id."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO fee_invoice_payments (
                organization_id, project_id, invoice_id, amount, paid_on, mode, reference
            )
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5::date, $6, $7)
            RETURNING id
            """,
            payment["organization_id"],
            payment["project_id"],
            payment["invoice_id"],
            payment["amount"],
            payment["paid_on"],
            payment["mode"],
            payment.get("reference"),
        )
        return str(row["id"])

    async def update_invoice_status(self, *, invoice_id: str, status: str) -> None:
        """Set an invoice to issued, partial, or paid."""
        await self.db_connection.execute(
            """
            UPDATE fee_invoices
            SET status = $2
            WHERE id = $1::uuid
            """,
            invoice_id,
            status,
        )

    async def _invoice_rows(self, where_sql: str, *args: Any) -> list[dict[str, Any]]:
        """Invoice headers matching a fixed WHERE clause."""
        rows = await self.db_connection.fetch(
            f"""
            SELECT
                id,
                invoice_number,
                organization_id,
                project_id,
                unit_id,
                due_date,
                invoice_date,
                billing_month,
                total_amount,
                status
            FROM fee_invoices
            {where_sql}
            """,
            *args,
        )
        return [dict(row) for row in rows]

    async def _with_children(
        self, invoices: list[dict[str, Any]], *, include_late_fee_rule: bool
    ) -> list[dict[str, Any]]:
        """Attach lines and payments. Collectible invoices also carry the fee head's late fee."""
        if not invoices:
            return []
        invoice_ids = [str(invoice["id"]) for invoice in invoices]
        lines = await self._lines_for(invoice_ids, include_late_fee_rule=include_late_fee_rule)
        payments = await self._payments_for(invoice_ids)
        posted = await self._posted_late_fees(invoice_ids) if include_late_fee_rule else {}
        for invoice in invoices:
            invoice_id = str(invoice["id"])
            invoice["lines"] = lines.get(invoice_id, [])
            invoice["payments"] = payments.get(invoice_id, [])
            if include_late_fee_rule:
                invoice["late_fees_posted"] = posted.get(invoice_id, [])
        return invoices

    async def _lines_for(
        self, invoice_ids: list[str], *, include_late_fee_rule: bool
    ) -> dict[str, list[dict[str, Any]]]:
        """Lines grouped by invoice id."""
        rule_sql = ", h.late_fee" if include_late_fee_rule else ""
        join_sql = "LEFT JOIN fee_heads h ON h.id = l.fee_head_id" if include_late_fee_rule else ""
        rows = await self.db_connection.fetch(
            f"""
            SELECT
                l.invoice_id,
                l.id,
                l.fee_head_id,
                l.kind::text AS kind,
                l.fee_head_version,
                l.description,
                l.line_total,
                l.line_role
                {rule_sql}
            FROM fee_invoice_lines l
            {join_sql}
            WHERE l.invoice_id = ANY($1::uuid[])
            ORDER BY l.created_at
            """,
            invoice_ids,
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            line = dict(row)
            invoice_id = str(line.pop("invoice_id"))
            if include_late_fee_rule:
                line["late_fee"] = _json_object(line.get("late_fee"))
            grouped[invoice_id].append(line)
        return grouped

    async def _payments_for(self, invoice_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Payments grouped by invoice id."""
        rows = await self.db_connection.fetch(
            """
            SELECT invoice_id, amount, paid_on
            FROM fee_invoice_payments
            WHERE invoice_id = ANY($1::uuid[])
            ORDER BY paid_on, created_at
            """,
            invoice_ids,
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            payment = dict(row)
            grouped[str(payment.pop("invoice_id"))].append(payment)
        return grouped

    async def _posted_late_fees(self, invoice_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Late-fee lines already written for these source invoices."""
        rows = await self.db_connection.fetch(
            """
            SELECT source_invoice_id, source_line_id, line_total, started_months
            FROM fee_invoice_lines
            WHERE line_role = 'late_fee'
              AND source_invoice_id = ANY($1::uuid[])
            """,
            invoice_ids,
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            posted = dict(row)
            grouped[str(posted.pop("source_invoice_id"))].append(posted)
        return grouped
