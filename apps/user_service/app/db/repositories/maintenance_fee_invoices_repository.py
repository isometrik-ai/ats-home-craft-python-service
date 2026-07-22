"""Maintenance fee invoice persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_INVOICE_SELECT = """
SELECT
  i.id::text AS id,
  i.organization_id::text AS organization_id,
  i.project_id::text AS project_id,
  i.unit_id::text AS unit_id,
  i.contact_unit_id::text AS contact_unit_id,
  i.unit_config_kind::text AS unit_config_kind,
  i.period_start,
  i.period_end,
  i.due_date,
  i.amount_minor,
  i.amount_paid_minor,
  i.currency,
  i.status::text AS status,
  i.retry_attempts,
  i.next_retry_at,
  i.reminders_sent,
  i.next_reminder_at,
  i.escalated_at,
  i.issued_at,
  i.paid_at,
  i.metadata,
  i.created_at,
  i.updated_at,
  u.code AS unit_code,
  u.unit_label
FROM maintenance_fee_invoices i
JOIN units u ON u.id = i.unit_id
WHERE i.organization_id = $1::uuid
"""


class MaintenanceFeeInvoicesRepository(BaseRepository):
    """Database operations for public.maintenance_fee_invoices."""

    async def insert(self, *, data: dict[str, Any]) -> dict[str, Any]:
        """Create an invoice row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO maintenance_fee_invoices (
                organization_id,
                project_id,
                unit_id,
                contact_unit_id,
                unit_config_kind,
                period_start,
                period_end,
                due_date,
                amount_minor,
                amount_paid_minor,
                currency,
                status,
                next_reminder_at,
                issued_at,
                metadata
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3::uuid,
                $4::uuid,
                $5::unit_config_kind,
                $6::date,
                $7::date,
                $8::date,
                $9,
                $10,
                $11,
                $12::maintenance_fee_invoice_status,
                $13,
                $14,
                $15::jsonb
            )
            RETURNING
                id::text AS id,
                organization_id::text AS organization_id,
                project_id::text AS project_id,
                unit_id::text AS unit_id,
                contact_unit_id::text AS contact_unit_id,
                unit_config_kind::text AS unit_config_kind,
                period_start,
                period_end,
                due_date,
                amount_minor,
                amount_paid_minor,
                currency,
                status::text AS status,
                retry_attempts,
                next_retry_at,
                reminders_sent,
                next_reminder_at,
                escalated_at,
                issued_at,
                paid_at,
                metadata,
                created_at,
                updated_at
            """,
            data["organization_id"],
            data["project_id"],
            data["unit_id"],
            data.get("contact_unit_id"),
            data["unit_config_kind"],
            data["period_start"],
            data["period_end"],
            data["due_date"],
            data["amount_minor"],
            data.get("amount_paid_minor", 0),
            data["currency"],
            data["status"],
            data.get("next_reminder_at"),
            data.get("issued_at"),
            data.get("metadata", {}),
        )
        return dict(row)

    async def get_by_id(self, *, organization_id: str, invoice_id: str) -> dict[str, Any] | None:
        """Fetch a single invoice."""
        row = await self.db_connection.fetchrow(
            f"{_INVOICE_SELECT} AND i.id = $2::uuid",
            organization_id,
            invoice_id,
        )
        return dict(row) if row else None

    async def list_by_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List invoices for a project with optional status filter."""
        params: list[Any] = [organization_id, project_id]
        status_sql = ""
        if status:
            status_sql = " AND i.status = $3::maintenance_fee_invoice_status"
            params.append(status)
        count_row = await self.db_connection.fetchrow(
            f"""
            SELECT COUNT(*)::int AS total
            FROM maintenance_fee_invoices i
            WHERE i.organization_id = $1::uuid
              AND i.project_id = $2::uuid
              {status_sql}
            """,
            *params,
        )
        total = int(count_row["total"]) if count_row else 0
        list_params = list(params)
        limit_param = len(list_params) + 1
        offset_param = len(list_params) + 2
        list_params.extend([limit, offset])
        rows = await self.db_connection.fetch(
            f"""
            {_INVOICE_SELECT}
              AND i.project_id = $2::uuid
              {status_sql}
            ORDER BY i.due_date DESC, i.created_at DESC
            LIMIT ${limit_param}
            OFFSET ${offset_param}
            """,
            *list_params,
        )
        return [dict(row) for row in rows], total

    async def list_by_contact_units(
        self,
        *,
        organization_id: str,
        contact_unit_ids: list[str],
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List invoices for resident-owned units."""
        if not contact_unit_ids:
            return [], 0
        count_row = await self.db_connection.fetchrow(
            """
            SELECT COUNT(*)::int AS total
            FROM maintenance_fee_invoices i
            WHERE i.organization_id = $1::uuid
              AND i.contact_unit_id = ANY($2::uuid[])
            """,
            organization_id,
            contact_unit_ids,
        )
        total = int(count_row["total"]) if count_row else 0
        rows = await self.db_connection.fetch(
            f"""
            {_INVOICE_SELECT}
              AND i.contact_unit_id = ANY($2::uuid[])
            ORDER BY i.due_date DESC, i.created_at DESC
            LIMIT $3
            OFFSET $4
            """,
            organization_id,
            contact_unit_ids,
            limit,
            offset,
        )
        return [dict(row) for row in rows], total

    async def list_due_for_reminders(
        self, *, organization_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch issued/overdue invoices due for reminder processing."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              i.id::text AS id,
              i.organization_id::text AS organization_id,
              i.project_id::text AS project_id,
              i.unit_id::text AS unit_id,
              i.contact_unit_id::text AS contact_unit_id,
              i.due_date,
              i.amount_minor,
              i.amount_paid_minor,
              i.currency,
              i.status::text AS status,
              i.reminders_sent,
              i.next_reminder_at,
              s.reminder_count,
              s.reminder_interval_days
            FROM maintenance_fee_invoices i
            JOIN project_fee_settings s ON s.project_id = i.project_id
            WHERE i.organization_id = $1::uuid
              AND i.status IN (
                'issued'::maintenance_fee_invoice_status,
                'overdue'::maintenance_fee_invoice_status
              )
              AND i.next_reminder_at IS NOT NULL
              AND i.next_reminder_at <= now()
              AND i.reminders_sent < s.reminder_count
            ORDER BY i.next_reminder_at ASC
            LIMIT $2
            """,
            organization_id,
            limit,
        )
        return [dict(row) for row in rows]

    async def list_due_for_retries(
        self, *, organization_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch failed/overdue invoices due for retry processing."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              i.id::text AS id,
              i.organization_id::text AS organization_id,
              i.project_id::text AS project_id,
              i.unit_id::text AS unit_id,
              i.contact_unit_id::text AS contact_unit_id,
              i.due_date,
              i.amount_minor,
              i.amount_paid_minor,
              i.currency,
              i.status::text AS status,
              i.retry_attempts,
              i.next_retry_at,
              s.retry_count,
              s.retry_interval_days,
              s.exhausted_retry_action::text AS exhausted_retry_action
            FROM maintenance_fee_invoices i
            JOIN project_fee_settings s ON s.project_id = i.project_id
            WHERE i.organization_id = $1::uuid
              AND i.status IN (
                'failed'::maintenance_fee_invoice_status,
                'overdue'::maintenance_fee_invoice_status
              )
              AND i.next_retry_at IS NOT NULL
              AND i.next_retry_at <= now()
              AND i.retry_attempts < s.retry_count
            ORDER BY i.next_retry_at ASC
            LIMIT $2
            """,
            organization_id,
            limit,
        )
        return [dict(row) for row in rows]

    async def update_invoice(
        self, *, organization_id: str, invoice_id: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Patch invoice fields."""
        allowed = {
            "status",
            "amount_paid_minor",
            "retry_attempts",
            "next_retry_at",
            "reminders_sent",
            "next_reminder_at",
            "escalated_at",
            "paid_at",
            "metadata",
        }
        sets: list[str] = []
        params: list[Any] = [organization_id, invoice_id]
        idx = 3
        for key, value in patch.items():
            if key not in allowed:
                continue
            if key == "status":
                sets.append(f"status = ${idx}::maintenance_fee_invoice_status")
            elif key == "metadata":
                sets.append(f"metadata = ${idx}::jsonb")
            else:
                sets.append(f"{key} = ${idx}")
            params.append(value)
            idx += 1
        if not sets:
            return await self.get_by_id(organization_id=organization_id, invoice_id=invoice_id)
        sets.append("updated_at = now()")
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE maintenance_fee_invoices
            SET {", ".join(sets)}
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            RETURNING
                id::text AS id,
                organization_id::text AS organization_id,
                project_id::text AS project_id,
                unit_id::text AS unit_id,
                contact_unit_id::text AS contact_unit_id,
                unit_config_kind::text AS unit_config_kind,
                period_start,
                period_end,
                due_date,
                amount_minor,
                amount_paid_minor,
                currency,
                status::text AS status,
                retry_attempts,
                next_retry_at,
                reminders_sent,
                next_reminder_at,
                escalated_at,
                issued_at,
                paid_at,
                metadata,
                created_at,
                updated_at
            """,
            *params,
        )
        return dict(row) if row else None

    async def sum_outstanding_by_unit(self, *, organization_id: str, unit_id: str) -> int:
        """Sum outstanding balance in minor units for a unit."""
        row = await self.db_connection.fetchrow(
            """
            SELECT COALESCE(SUM(amount_minor - amount_paid_minor), 0)::bigint AS outstanding
            FROM maintenance_fee_invoices
            WHERE organization_id = $1::uuid
              AND unit_id = $2::uuid
              AND status IN (
                'issued'::maintenance_fee_invoice_status,
                'partially_paid'::maintenance_fee_invoice_status,
                'overdue'::maintenance_fee_invoice_status,
                'failed'::maintenance_fee_invoice_status,
                'escalated'::maintenance_fee_invoice_status
              )
            """,
            organization_id,
            unit_id,
        )
        return int(row["outstanding"]) if row else 0

    async def latest_monthly_fee_by_unit(self, *, organization_id: str, unit_id: str) -> int | None:
        """Return the most recent issued invoice amount as monthly fee proxy."""
        row = await self.db_connection.fetchrow(
            """
            SELECT amount_minor
            FROM maintenance_fee_invoices
            WHERE organization_id = $1::uuid
              AND unit_id = $2::uuid
              AND status NOT IN (
                'cancelled'::maintenance_fee_invoice_status,
                'draft'::maintenance_fee_invoice_status
              )
            ORDER BY period_start DESC
            LIMIT 1
            """,
            organization_id,
            unit_id,
        )
        return int(row["amount_minor"]) if row else None
