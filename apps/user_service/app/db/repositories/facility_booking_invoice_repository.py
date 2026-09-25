"""Persistence for facility booking invoices."""

from __future__ import annotations

import json
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.utils.common_utils import serialize_jsonb_param

_INVOICE_SELECT = """
    SELECT
        i.*,
        NULLIF(BTRIM(CONCAT_WS(' ', c.first_name, c.last_name)), '') AS contact_name
    FROM facility_booking_invoices i
    LEFT JOIN contacts c ON c.id = i.contact_id
"""


def _decode_lines(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("lines")
    if isinstance(value, str):
        row["lines"] = json.loads(value)
    return row


class FacilityBookingInvoiceRepository(BaseRepository):
    """facility_booking_invoices."""

    async def insert(self, data: dict[str, Any]) -> dict[str, Any]:
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO facility_booking_invoices (
                organization_id, project_id, contact_id, number, status, lines,
                total, period_label, due_date, created_by_user_id
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4, $5::facility_booking_invoice_status,
                $6::jsonb, $7, $8, $9::date, $10::uuid
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["contact_id"],
            data["number"],
            data.get("status") or "issued",
            serialize_jsonb_param("lines", data.get("lines") or [], frozenset({"lines"})),
            data["total"],
            data["period_label"],
            data["due_date"],
            data.get("created_by_user_id"),
        )
        return _decode_lines(dict(row))

    async def count_for_year(self, *, organization_id: str, project_id: str, year: int) -> int:
        value = await self.db_connection.fetchval(
            """
            SELECT COUNT(*)::int
            FROM facility_booking_invoices
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND EXTRACT(YEAR FROM created_at) = $3
            """,
            organization_id,
            project_id,
            year,
        )
        return int(value or 0)

    async def get(
        self, *, organization_id: str, project_id: str, invoice_id: str
    ) -> dict[str, Any] | None:
        row = await self.db_connection.fetchrow(
            f"""
            {_INVOICE_SELECT}
            WHERE i.organization_id = $1::uuid
              AND i.project_id = $2::uuid
              AND i.id = $3::uuid
            """,
            organization_id,
            project_id,
            invoice_id,
        )
        return _decode_lines(dict(row)) if row else None

    async def list_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        contact_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = [
            "i.organization_id = $1::uuid",
            "i.project_id = $2::uuid",
        ]
        values: list[Any] = [organization_id, project_id]
        idx = 3
        if contact_id:
            conditions.append(f"i.contact_id = ${idx}::uuid")
            values.append(contact_id)
            idx += 1
        if status:
            conditions.append(f"i.status = ${idx}::facility_booking_invoice_status")
            values.append(status)
            idx += 1
        where_sql = " AND ".join(conditions)
        total = await self.db_connection.fetchval(
            f"SELECT COUNT(*)::int FROM facility_booking_invoices i WHERE {where_sql}",
            *values,
        )
        rows = await self.db_connection.fetch(
            f"""
            {_INVOICE_SELECT}
            WHERE {where_sql}
            ORDER BY i.created_at DESC
            OFFSET ${idx} LIMIT ${idx + 1}
            """,
            *values,
            (page - 1) * page_size,
            page_size,
        )
        return [_decode_lines(dict(row)) for row in rows], int(total or 0)

    async def mark_paid(self, *, invoice_id: str, update: dict[str, Any]) -> dict[str, Any]:
        row = await self.db_connection.fetchrow(
            """
            UPDATE facility_booking_invoices
            SET status = 'paid',
                paid_at = $2,
                paid_via = $3::facility_booking_payment_method,
                payment_ref = $4,
                collected_by = $5,
                notes = $6,
                updated_at = NOW()
            WHERE id = $1::uuid
            RETURNING *
            """,
            invoice_id,
            update["paid_at"],
            update["paid_via"],
            update.get("payment_ref"),
            update.get("collected_by"),
            update.get("notes"),
        )
        return _decode_lines(dict(row))
