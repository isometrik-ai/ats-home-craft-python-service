"""Payments repository."""

from __future__ import annotations

from typing import Any

from apps.work_order_service.app.db.repositories.base import ScopedRepository
from apps.work_order_service.app.utils.records import record_to_dict


class PaymentsRepository(ScopedRepository):
    """Database access for payments."""

    table = "payments"

    async def list(
        self,
        *,
        organization_id: str,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
        invoice_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        offset = (page - 1) * page_size
        params: list[Any] = [organization_id, project_id]
        invoice_clause = ""
        if invoice_id:
            params.append(invoice_id)
            invoice_clause = f" AND invoice_id = ${len(params)}::uuid"
        total = await self.conn.fetchval(
            f"""
            SELECT COUNT(*) FROM work_order.payments
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{invoice_clause}
            """,
            *params,
        )
        params.extend([page_size, offset])
        rows = await self.conn.fetch(
            f"""
            SELECT * FROM work_order.payments
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{invoice_clause}
            ORDER BY created_at DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [record_to_dict(r) for r in rows], int(total or 0)

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create."""
        row = await self.conn.fetchrow(
            """
            INSERT INTO work_order.payments (
                organization_id, project_id, invoice_id, work_order_id, amount_minor,
                currency, method, reference, payment_date, status, receipt_path, notes
            ) VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5,
                COALESCE($6, 'INR'),
                COALESCE($7::work_order.work_order_payment_method, 'bank_transfer'),
                $8, $9::date,
                COALESCE($10::work_order.work_order_payment_status, 'completed'),
                $11, $12
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data.get("invoice_id"),
            data.get("work_order_id"),
            data["amount_minor"],
            data.get("currency"),
            data.get("method"),
            data.get("reference"),
            data.get("payment_date"),
            data.get("status"),
            data.get("receipt_path"),
            data.get("notes"),
        )
        return record_to_dict(row)

    async def update(self, entity_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update."""
        row = await self.conn.fetchrow(
            """
            UPDATE work_order.payments
            SET amount_minor = COALESCE($4, amount_minor),
                currency = COALESCE($5, currency),
                method = COALESCE($6::work_order.work_order_payment_method, method),
                reference = COALESCE($7, reference),
                payment_date = COALESCE($8::date, payment_date),
                status = COALESCE($9::work_order.work_order_payment_status, status),
                receipt_path = COALESCE($10, receipt_path),
                notes = COALESCE($11, notes),
                updated_at = now()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND project_id = $3::uuid
              AND record_status = 'active'
            RETURNING *
            """,
            entity_id,
            data["organization_id"],
            data["project_id"],
            data.get("amount_minor"),
            data.get("currency"),
            data.get("method"),
            data.get("reference"),
            data.get("payment_date"),
            data.get("status"),
            data.get("receipt_path"),
            data.get("notes"),
        )
        return record_to_dict(row) if row else None
