"""Work orders repository."""

from __future__ import annotations

from datetime import date
from typing import Any

from apps.work_order_service.app.db.repositories.base import ScopedRepository
from apps.work_order_service.app.utils.records import (
    coerce_date,
    jsonb_bind,
    jsonb_bind_required,
    jsonb_bind_update,
    record_to_dict,
)


class WorkOrderRepository(ScopedRepository):
    """Database access for work order."""

    table = "work_orders"

    async def list(
        self,
        *,
        organization_id: str,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
        state: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        offset = (page - 1) * page_size
        params: list[Any] = [organization_id, project_id]
        state_clause = ""
        if state:
            params.append(state)
            state_clause = f" AND state = ${len(params)}::work_order.work_order_work_order_state"
        total = await self.conn.fetchval(
            f"""
            SELECT COUNT(*) FROM work_order.work_orders
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{state_clause}
            """,
            *params,
        )
        params.extend([page_size, offset])
        rows = await self.conn.fetch(
            f"""
            SELECT * FROM work_order.work_orders
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{state_clause}
            ORDER BY scheduled_date NULLS LAST, created_at DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [record_to_dict(r) for r in rows], int(total or 0)

    async def get_by_vendor_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        """Get by vendor token hash."""
        row = await self.conn.fetchrow(
            """
            SELECT * FROM work_order.work_orders
            WHERE vendor_token_hash = $1 AND record_status = 'active'
            LIMIT 1
            """,
            token_hash,
        )
        return record_to_dict(row) if row else None

    async def exists_for_contract_date(
        self,
        *,
        contract_id: str,
        scheduled_date: str,
    ) -> bool:
        """Exists for contract date."""
        val = await self.conn.fetchval(
            """
            SELECT 1 FROM work_order.work_orders
            WHERE contract_id = $1::uuid
              AND scheduled_date = $2::date
              AND source = 'contract'
              AND state NOT IN ('terminated')
              AND record_status = 'active'
            LIMIT 1
            """,
            contract_id,
            coerce_date(scheduled_date),
        )
        return val is not None

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create."""
        row = await self.conn.fetchrow(
            """
            INSERT INTO work_order.work_orders (
                organization_id, project_id, title, description, asset_ids, contract_id,
                vendor_id, state, priority, source, scheduled_date, assignee_name,
                assignee_user_id, vendor_token_hash, timeline, access_notes,
                form_template_id, pre_start_form_template_id, estimated_cost_minor,
                line_items, form_values, pre_start_form_values, is_recurring,
                recurring_frequency, recurring_days, recurring_end_date, recurring_parent_id
            ) VALUES (
                $1::uuid, $2::uuid, $3, $4, $5::uuid[], $6::uuid, $7::uuid,
                COALESCE($8::work_order.work_order_work_order_state, 'upcoming'),
                COALESCE($9::work_order.work_order_work_order_priority, 'medium'),
                COALESCE($10::work_order.work_order_work_order_source, 'ad_hoc'),
                $11::date, $12, $13::uuid, $14, $15::jsonb, $16, $17::uuid, $18::uuid,
                $19, COALESCE($20::jsonb, '[]'::jsonb), COALESCE($21::jsonb, '{}'::jsonb),
                COALESCE($22::jsonb, '{}'::jsonb), COALESCE($23, false),
                $24::work_order.work_order_visit_frequency, $25::smallint[], $26::date, $27::uuid
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["title"],
            data.get("description"),
            data.get("asset_ids") or [],
            data.get("contract_id"),
            data.get("vendor_id"),
            data.get("state"),
            data.get("priority"),
            data.get("source"),
            coerce_date(data.get("scheduled_date")),
            data.get("assignee_name"),
            data.get("assignee_user_id"),
            data.get("vendor_token_hash"),
            jsonb_bind_required(data.get("timeline")),
            data.get("access_notes"),
            data.get("form_template_id"),
            data.get("pre_start_form_template_id"),
            data.get("estimated_cost_minor"),
            jsonb_bind(data.get("line_items")),
            jsonb_bind(data.get("form_values")),
            jsonb_bind(data.get("pre_start_form_values")),
            data.get("is_recurring", False),
            data.get("recurring_frequency"),
            data.get("recurring_days") or [],
            data.get("recurring_end_date"),
            data.get("recurring_parent_id"),
        )
        return record_to_dict(row)

    async def update(self, entity_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update."""
        row = await self.conn.fetchrow(
            """
            UPDATE work_order.work_orders
            SET title = COALESCE($4, title),
                description = COALESCE($5, description),
                state = COALESCE($6::work_order.work_order_work_order_state, state),
                priority = COALESCE($7::work_order.work_order_work_order_priority, priority),
                scheduled_date = COALESCE($8::date, scheduled_date),
                vendor_id = COALESCE($9::uuid, vendor_id),
                started_at = COALESCE($10::timestamptz, started_at),
                completed_at = COALESCE($11::timestamptz, completed_at),
                termination_reason = COALESCE($12, termination_reason),
                asset_ids = COALESCE($13::uuid[], asset_ids),
                assignee_name = COALESCE($14, assignee_name),
                assignee_user_id = COALESCE($15::uuid, assignee_user_id),
                access_notes = COALESCE($16, access_notes),
                form_template_id = COALESCE($17::uuid, form_template_id),
                pre_start_form_template_id = COALESCE($18::uuid, pre_start_form_template_id),
                estimated_cost_minor = COALESCE($19, estimated_cost_minor),
                line_items = COALESCE($20::jsonb, line_items),
                form_values = COALESCE($21::jsonb, form_values),
                pre_start_form_values = COALESCE($22::jsonb, pre_start_form_values),
                is_recurring = COALESCE($23, is_recurring),
                recurring_frequency = COALESCE(
                    $24::work_order.work_order_visit_frequency, recurring_frequency
                ),
                recurring_days = COALESCE($25::smallint[], recurring_days),
                recurring_end_date = COALESCE($26::date, recurring_end_date),
                invoice_ids = COALESCE($27::uuid[], invoice_ids),
                updated_at = now()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND project_id = $3::uuid
              AND record_status = 'active'
            RETURNING *
            """,
            entity_id,
            data["organization_id"],
            data["project_id"],
            data.get("title"),
            data.get("description"),
            data.get("state"),
            data.get("priority"),
            coerce_date(data.get("scheduled_date")),
            data.get("vendor_id"),
            data.get("started_at"),
            data.get("completed_at"),
            data.get("termination_reason"),
            data["asset_ids"] if "asset_ids" in data else None,
            data.get("assignee_name"),
            data.get("assignee_user_id"),
            data.get("access_notes"),
            data.get("form_template_id"),
            data.get("pre_start_form_template_id"),
            data.get("estimated_cost_minor"),
            jsonb_bind_update(data["line_items"]) if "line_items" in data else None,
            jsonb_bind_update(data["form_values"]) if "form_values" in data else None,
            jsonb_bind_update(data["pre_start_form_values"])
            if "pre_start_form_values" in data
            else None,
            data["is_recurring"] if "is_recurring" in data else None,
            data.get("recurring_frequency"),
            data["recurring_days"] if "recurring_days" in data else None,
            data.get("recurring_end_date"),
            data["invoice_ids"] if "invoice_ids" in data else None,
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
            UPDATE work_order.work_orders
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

    async def cancel_contract_work_orders(self, contract_id: str) -> int:
        """Cancel contract work orders."""
        result = await self.conn.execute(
            """
            UPDATE work_order.work_orders
            SET state = 'terminated', updated_at = now()
            WHERE contract_id = $1::uuid
              AND source = 'contract'
              AND state = 'upcoming'
              AND record_status = 'active'
            """,
            contract_id,
        )
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def existing_contract_schedule_dates(self, contract_id: str) -> set[str]:
        """Existing contract schedule dates."""
        rows = await self.conn.fetch(
            """
            SELECT scheduled_date FROM work_order.work_orders
            WHERE contract_id = $1::uuid AND source = 'contract'
              AND state != 'terminated' AND record_status = 'active'
            """,
            contract_id,
        )
        return {str(r["scheduled_date"])[:10] for r in rows if r["scheduled_date"]}

    async def list_recurring_templates(
        self, organization_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List recurring templates."""
        params: list[Any] = []
        org_clause = ""
        if organization_id:
            params.append(organization_id)
            org_clause = f" AND organization_id = ${len(params)}::uuid"
        rows = await self.conn.fetch(
            f"""
            SELECT * FROM work_order.work_orders
            WHERE is_recurring = true AND state != 'terminated'
              AND record_status = 'active'{org_clause}
            """,
            *params,
        )
        return [record_to_dict(r) for r in rows]

    async def recurring_child_dates(self, template_id: str) -> set[str]:
        """Recurring child dates."""
        rows = await self.conn.fetch(
            """
            SELECT scheduled_date FROM work_order.work_orders
            WHERE recurring_parent_id = $1::uuid AND state != 'terminated'
              AND record_status = 'active'
            """,
            template_id,
        )
        return {str(r["scheduled_date"])[:10] for r in rows if r["scheduled_date"]}

    async def cancel_recurring_children(self, template_id: str, note: str) -> int:
        """Cancel recurring children."""
        result = await self.conn.execute(
            """
            UPDATE work_order.work_orders
            SET state = 'terminated',
                timeline = timeline || $2::jsonb,
                updated_at = now()
            WHERE recurring_parent_id = $1::uuid
              AND state = 'upcoming' AND started_at IS NULL
              AND record_status = 'active'
            """,
            template_id,
            jsonb_bind_required(
                [
                    {
                        "type": "status_changed",
                        "by": "Scheduler",
                        "note": note,
                    }
                ]
            ),
        )
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def update_recurring_next_date(
        self, work_order_id: str, next_date: date | str | None
    ) -> None:
        """Update recurring next date."""
        await self.conn.execute(
            """
            UPDATE work_order.work_orders
            SET recurring_next_date = $2::date, updated_at = now()
            WHERE id = $1::uuid
            """,
            work_order_id,
            coerce_date(next_date),
        )
