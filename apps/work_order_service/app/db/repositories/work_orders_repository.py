"""Work orders repository."""

from __future__ import annotations

import json
from typing import Any

from apps.work_order_service.app.db.repositories.base import ScopedRepository
from apps.work_order_service.app.utils.records import record_to_dict


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
            scheduled_date,
        )
        return val is not None

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create."""
        row = await self.conn.fetchrow(
            """
            INSERT INTO work_order.work_orders (
                organization_id, project_id, title, description, asset_ids, contract_id,
                company_id, state, priority, source, scheduled_date, assignee_name,
                assignee_user_id, vendor_token_hash, timeline, access_notes,
                form_template_id, is_recurring, recurring_frequency, recurring_days,
                recurring_end_date, recurring_parent_id
            ) VALUES (
                $1::uuid, $2::uuid, $3, $4, $5::uuid[], $6::uuid, $7::uuid,
                COALESCE($8::work_order.work_order_work_order_state, 'upcoming'),
                COALESCE($9::work_order.work_order_work_order_priority, 'medium'),
                COALESCE($10::work_order.work_order_work_order_source, 'ad_hoc'),
                $11::date, $12, $13::uuid, $14, $15::jsonb, $16, $17::uuid,
                COALESCE($18, false), $19::work_order.work_order_visit_frequency,
                $20::smallint[], $21::date, $22::uuid
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["title"],
            data.get("description"),
            data.get("asset_ids") or [],
            data.get("contract_id"),
            data.get("company_id"),
            data.get("state"),
            data.get("priority"),
            data.get("source"),
            data.get("scheduled_date"),
            data.get("assignee_name"),
            data.get("assignee_user_id"),
            data.get("vendor_token_hash"),
            json.dumps(data.get("timeline") or []),
            data.get("access_notes"),
            data.get("form_template_id"),
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
                timeline = COALESCE($9::jsonb, timeline),
                company_id = COALESCE($10::uuid, company_id),
                started_at = COALESCE($11::timestamptz, started_at),
                completed_at = COALESCE($12::timestamptz, completed_at),
                termination_reason = COALESCE($13, termination_reason),
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
            data.get("scheduled_date"),
            json.dumps(data["timeline"]) if "timeline" in data else None,
            data.get("company_id"),
            data.get("started_at"),
            data.get("completed_at"),
            data.get("termination_reason"),
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
            json.dumps([evt]),
        )
        if not row:
            return None
        timeline = row["timeline"]
        return timeline if isinstance(timeline, list) else json.loads(timeline)

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
            json.dumps(
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

    async def update_recurring_next_date(self, work_order_id: str, next_date: str | None) -> None:
        """Update recurring next date."""
        await self.conn.execute(
            """
            UPDATE work_order.work_orders
            SET recurring_next_date = $2::date, updated_at = now()
            WHERE id = $1::uuid
            """,
            work_order_id,
            next_date,
        )
