"""Vendor invoices repository."""

from __future__ import annotations

from typing import Any

from apps.work_order_service.app.db.repositories.base import ScopedRepository
from apps.work_order_service.app.utils.records import (
    jsonb_bind,
    jsonb_bind_required,
    jsonb_bind_update,
    record_to_dict,
)


class InvoicesRepository(ScopedRepository):
    """Database access for invoices."""

    table = "vendor_invoices"

    async def list(
        self,
        *,
        organization_id: str,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
        work_order_id: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        offset = (page - 1) * page_size
        params: list[Any] = [organization_id, project_id]
        filters = ""
        if work_order_id:
            params.append(work_order_id)
            filters += f" AND work_order_id = ${len(params)}::uuid"
        if status:
            params.append(status)
            filters += f" AND status = ${len(params)}::work_order.work_order_invoice_status"
        total = await self.conn.fetchval(
            f"""
            SELECT COUNT(*) FROM work_order.vendor_invoices
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{filters}
            """,
            *params,
        )
        params.extend([page_size, offset])
        rows = await self.conn.fetch(
            f"""
            SELECT * FROM work_order.vendor_invoices
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{filters}
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
            INSERT INTO work_order.vendor_invoices (
                organization_id, project_id, work_order_id, vendor_id, invoice_number,
                invoice_date, line_items, subtotal_minor, tax_minor, total_minor,
                currency, status, file_paths, timeline, revisions
            ) VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6::date,
                COALESCE($7::jsonb, '[]'::jsonb), COALESCE($8, 0), COALESCE($9, 0),
                COALESCE($10, 0), COALESCE($11, 'INR'),
                COALESCE($12::work_order.work_order_invoice_status, 'submitted'),
                COALESCE($13::text[], '{}'), COALESCE($14::jsonb, '[]'::jsonb),
                COALESCE($15::jsonb, '[]'::jsonb)
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["work_order_id"],
            data["vendor_id"],
            data["invoice_number"],
            data.get("invoice_date"),
            jsonb_bind(data.get("line_items")),
            data.get("subtotal_minor"),
            data.get("tax_minor"),
            data.get("total_minor"),
            data.get("currency"),
            data.get("status"),
            data.get("file_paths") or [],
            jsonb_bind(data.get("timeline")),
            jsonb_bind(data.get("revisions")),
        )
        return record_to_dict(row)

    async def update(self, entity_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update."""
        row = await self.conn.fetchrow(
            """
            UPDATE work_order.vendor_invoices
            SET invoice_number = COALESCE($4, invoice_number),
                invoice_date = COALESCE($5::date, invoice_date),
                line_items = COALESCE($6::jsonb, line_items),
                subtotal_minor = COALESCE($7, subtotal_minor),
                tax_minor = COALESCE($8, tax_minor),
                total_minor = COALESCE($9, total_minor),
                currency = COALESCE($10, currency),
                status = COALESCE($11::work_order.work_order_invoice_status, status),
                file_paths = COALESCE($12::text[], file_paths),
                timeline = COALESCE($13::jsonb, timeline),
                revisions = COALESCE($14::jsonb, revisions),
                payment_id = COALESCE($15::uuid, payment_id),
                updated_at = now()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND project_id = $3::uuid
              AND record_status = 'active'
            RETURNING *
            """,
            entity_id,
            data["organization_id"],
            data["project_id"],
            data.get("invoice_number"),
            data.get("invoice_date"),
            jsonb_bind_update(data["line_items"]) if "line_items" in data else None,
            data.get("subtotal_minor"),
            data.get("tax_minor"),
            data.get("total_minor"),
            data.get("currency"),
            data.get("status"),
            data["file_paths"] if "file_paths" in data else None,
            jsonb_bind(data["timeline"]) if "timeline" in data else None,
            jsonb_bind_update(data["revisions"]) if "revisions" in data else None,
            data.get("payment_id"),
        )
        return record_to_dict(row) if row else None

    async def append_timeline(
        self,
        *,
        entity_id: str,
        organization_id: str,
        project_id: str,
        event: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        """Append timeline."""
        from apps.work_order_service.app.utils.records import new_timeline_event

        evt = new_timeline_event(event)
        row = await self.conn.fetchrow(
            """
            UPDATE work_order.vendor_invoices
            SET timeline = timeline || $4::jsonb, updated_at = now()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND project_id = $3::uuid
              AND record_status = 'active'
            RETURNING timeline
            """,
            entity_id,
            organization_id,
            project_id,
            jsonb_bind_required([evt]),
        )
        if not row:
            return None
        from apps.work_order_service.app.utils.records import parse_json_field

        return parse_json_field(row["timeline"])
