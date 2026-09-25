"""Persistence for facility booking ledger entries."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import (
    INVOICEABLE_LEDGER_TYPES,
    UNBILLED_LEDGER_TYPES,
)

_LEDGER_SELECT = """
    SELECT
        e.id::text AS id,
        e.contact_id::text AS contact_id,
        e.reservation_id::text AS reservation_id,
        e.invoice_id::text AS invoice_id,
        e.entry_type::text AS entry_type,
        e.description,
        e.amount,
        e.method,
        e.posted_at,
        NULLIF(BTRIM(CONCAT_WS(' ', c.first_name, c.last_name)), '') AS contact_name,
        f.name AS facility_name
    FROM facility_booking_ledger_entries e
    LEFT JOIN contacts c ON c.id = e.contact_id
    LEFT JOIN facility_reservations r ON r.id = e.reservation_id
    LEFT JOIN facilities f ON f.id = r.facility_id
"""


class FacilityBookingLedgerRepository(BaseRepository):
    """facility_booking_ledger_entries."""

    async def insert(self, data: dict[str, Any]) -> dict[str, Any]:
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO facility_booking_ledger_entries (
                organization_id, project_id, contact_id, reservation_id, invoice_id,
                entry_type, description, amount, method, posted_at, created_by_user_id
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid,
                $6::facility_booking_ledger_type, $7, $8, $9, COALESCE($10, now()), $11::uuid
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["contact_id"],
            data.get("reservation_id"),
            data.get("invoice_id"),
            data["entry_type"],
            data["description"],
            data["amount"],
            data.get("method"),
            data.get("posted_at"),
            data.get("created_by_user_id"),
        )
        return dict(row)

    async def list_for_reservation(
        self, *, organization_id: str, project_id: str, reservation_id: str
    ) -> list[dict[str, Any]]:
        rows = await self.db_connection.fetch(
            f"""
            {_LEDGER_SELECT}
            WHERE e.organization_id = $1::uuid
              AND e.project_id = $2::uuid
              AND e.reservation_id = $3::uuid
            ORDER BY e.posted_at, e.created_at
            """,
            organization_id,
            project_id,
            reservation_id,
        )
        return [dict(row) for row in rows]

    async def list_for_contact(
        self,
        *,
        organization_id: str,
        project_id: str,
        contact_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        total = await self.db_connection.fetchval(
            """
            SELECT COUNT(*)::int
            FROM facility_booking_ledger_entries
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND contact_id = $3::uuid
            """,
            organization_id,
            project_id,
            contact_id,
        )
        rows = await self.db_connection.fetch(
            f"""
            {_LEDGER_SELECT}
            WHERE e.organization_id = $1::uuid
              AND e.project_id = $2::uuid
              AND e.contact_id = $3::uuid
            ORDER BY e.posted_at DESC, e.created_at DESC
            OFFSET $4 LIMIT $5
            """,
            organization_id,
            project_id,
            contact_id,
            (page - 1) * page_size,
            page_size,
        )
        return [dict(row) for row in rows], int(total or 0)

    async def list_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        contact_id: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = [
            "e.organization_id = $1::uuid",
            "e.project_id = $2::uuid",
        ]
        values: list[Any] = [organization_id, project_id]
        idx = 3
        if contact_id:
            conditions.append(f"e.contact_id = ${idx}::uuid")
            values.append(contact_id)
            idx += 1
        where_sql = " AND ".join(conditions)
        total = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)::int
            FROM facility_booking_ledger_entries e
            WHERE {where_sql}
            """,
            *values,
        )
        rows = await self.db_connection.fetch(
            f"""
            {_LEDGER_SELECT}
            WHERE {where_sql}
            ORDER BY e.posted_at DESC, e.created_at DESC
            OFFSET ${idx} LIMIT ${idx + 1}
            """,
            *values,
            (page - 1) * page_size,
            page_size,
        )
        return [dict(row) for row in rows], int(total or 0)

    async def contact_balance(
        self, *, organization_id: str, project_id: str, contact_id: str
    ) -> int:
        value = await self.db_connection.fetchval(
            """
            SELECT COALESCE(SUM(amount), 0)::int
            FROM facility_booking_ledger_entries
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND contact_id = $3::uuid
            """,
            organization_id,
            project_id,
            contact_id,
        )
        return int(value or 0)

    async def list_unbilled(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        rows = await self.db_connection.fetch(
            f"""
            {_LEDGER_SELECT}
            WHERE e.organization_id = $1::uuid
              AND e.project_id = $2::uuid
              AND e.invoice_id IS NULL
              AND e.entry_type::text = ANY($3::text[])
            ORDER BY e.posted_at, e.created_at
            """,
            organization_id,
            project_id,
            list(UNBILLED_LEDGER_TYPES),
        )
        return [dict(row) for row in rows]

    async def list_invoiceable(
        self,
        *,
        organization_id: str,
        project_id: str,
        contact_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = await self.db_connection.fetch(
            f"""
            {_LEDGER_SELECT}
            WHERE e.organization_id = $1::uuid
              AND e.project_id = $2::uuid
              AND e.invoice_id IS NULL
              AND e.amount > 0
              AND e.entry_type::text = ANY($3::text[])
              AND ($4::uuid[] IS NULL OR e.contact_id = ANY($4::uuid[]))
            ORDER BY e.contact_id, e.posted_at, e.created_at
            """,
            organization_id,
            project_id,
            list(INVOICEABLE_LEDGER_TYPES),
            contact_ids,
        )
        return [dict(row) for row in rows]

    async def attach_invoice(self, *, entry_ids: list[str], invoice_id: str) -> None:
        if not entry_ids:
            return
        await self.db_connection.execute(
            """
            UPDATE facility_booking_ledger_entries
            SET invoice_id = $2::uuid
            WHERE id = ANY($1::uuid[])
            """,
            entry_ids,
            invoice_id,
        )

    async def list_payments_for_month(
        self, *, organization_id: str, project_id: str, year: int, month: int
    ) -> list[dict[str, Any]]:
        rows = await self.db_connection.fetch(
            """
            SELECT method, amount, posted_at
            FROM facility_booking_ledger_entries
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND entry_type = 'payment'
              AND EXTRACT(YEAR FROM posted_at) = $3
              AND EXTRACT(MONTH FROM posted_at) = $4
            """,
            organization_id,
            project_id,
            year,
            month,
        )
        return [dict(row) for row in rows]
