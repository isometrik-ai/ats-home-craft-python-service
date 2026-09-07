"""Maintenance contracts repository."""

from __future__ import annotations

from typing import Any

from apps.work_order_service.app.db.repositories.base import ScopedRepository
from apps.work_order_service.app.utils.records import record_to_dict


class ContractsRepository(ScopedRepository):
    """Database access for contracts."""

    table = "maintenance_contracts"

    async def list(
        self,
        *,
        organization_id: str,
        project_id: str,
        page: int = 1,
        page_size: int = 50,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List."""
        offset = (page - 1) * page_size
        params: list[Any] = [organization_id, project_id]
        status_clause = ""
        if status:
            params.append(status)
            status_clause = f" AND status = ${len(params)}::work_order.work_order_contract_status"
        total = await self.conn.fetchval(
            f"""
            SELECT COUNT(*) FROM work_order.maintenance_contracts
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{status_clause}
            """,
            *params,
        )
        params.extend([page_size, offset])
        rows = await self.conn.fetch(
            f"""
            SELECT * FROM work_order.maintenance_contracts
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND record_status = 'active'{status_clause}
            ORDER BY created_at DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [record_to_dict(r) for r in rows], int(total or 0)

    async def list_active_for_scheduler(
        self, organization_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List active for scheduler."""
        clause = "record_status = 'active'"
        params: list[Any] = []
        if organization_id:
            params.append(organization_id)
            clause += f" AND organization_id = ${len(params)}::uuid"
        rows = await self.conn.fetch(
            f"SELECT * FROM work_order.maintenance_contracts WHERE {clause}",
            *params,
        )
        return [record_to_dict(r) for r in rows]

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create."""
        row = await self.conn.fetchrow(
            """
            INSERT INTO work_order.maintenance_contracts (
                organization_id, project_id, title, company_id, asset_ids, start_date,
                end_date, visit_frequency, payment_frequency, value_minor, currency,
                status, next_visit_date, last_serviced_date, auto_generate_lead_days,
                scope_included, scope_excluded, form_template_id, pre_start_form_template_id,
                document_paths
            ) VALUES (
                $1::uuid, $2::uuid, $3, $4::uuid, COALESCE($5::uuid[], '{}'),
                $6::date, $7::date,
                COALESCE($8::work_order.work_order_visit_frequency, 'quarterly'),
                COALESCE($9::work_order.work_order_payment_frequency, 'quarterly'),
                $10, COALESCE($11, 'INR'),
                COALESCE($12::work_order.work_order_contract_status, 'active'),
                $13::date, $14::date, $15, $16, $17, $18::uuid, $19::uuid,
                COALESCE($20::text[], '{}')
            )
            RETURNING *
            """,
            data["organization_id"],
            data["project_id"],
            data["title"],
            data["company_id"],
            data.get("asset_ids") or [],
            data["start_date"],
            data.get("end_date"),
            data.get("visit_frequency"),
            data.get("payment_frequency"),
            data.get("value_minor"),
            data.get("currency"),
            data.get("status"),
            data.get("next_visit_date"),
            data.get("last_serviced_date"),
            data.get("auto_generate_lead_days"),
            data.get("scope_included"),
            data.get("scope_excluded"),
            data.get("form_template_id"),
            data.get("pre_start_form_template_id"),
            data.get("document_paths") or [],
        )
        return record_to_dict(row)

    async def update(self, entity_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """Update."""
        row = await self.conn.fetchrow(
            """
            UPDATE work_order.maintenance_contracts
            SET title = COALESCE($4, title),
                company_id = COALESCE($5::uuid, company_id),
                asset_ids = COALESCE($6::uuid[], asset_ids),
                start_date = COALESCE($7::date, start_date),
                end_date = COALESCE($8::date, end_date),
                visit_frequency = COALESCE(
                    $9::work_order.work_order_visit_frequency, visit_frequency
                ),
                payment_frequency = COALESCE(
                    $10::work_order.work_order_payment_frequency, payment_frequency
                ),
                value_minor = COALESCE($11, value_minor),
                currency = COALESCE($12, currency),
                status = COALESCE($13::work_order.work_order_contract_status, status),
                next_visit_date = COALESCE($14::date, next_visit_date),
                last_serviced_date = COALESCE($15::date, last_serviced_date),
                auto_generate_lead_days = COALESCE($16, auto_generate_lead_days),
                scope_included = COALESCE($17, scope_included),
                scope_excluded = COALESCE($18, scope_excluded),
                form_template_id = COALESCE($19::uuid, form_template_id),
                pre_start_form_template_id = COALESCE($20::uuid, pre_start_form_template_id),
                document_paths = COALESCE($21::text[], document_paths),
                termination_reason = COALESCE($22, termination_reason),
                updated_at = now()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND project_id = $3::uuid
              AND record_status = 'active'
            RETURNING *
            """,
            entity_id,
            data["organization_id"],
            data["project_id"],
            data.get("title"),
            data.get("company_id"),
            data.get("asset_ids"),
            data.get("start_date"),
            data.get("end_date"),
            data.get("visit_frequency"),
            data.get("payment_frequency"),
            data.get("value_minor"),
            data.get("currency"),
            data.get("status"),
            data.get("next_visit_date"),
            data.get("last_serviced_date"),
            data.get("auto_generate_lead_days"),
            data.get("scope_included"),
            data.get("scope_excluded"),
            data.get("form_template_id"),
            data.get("pre_start_form_template_id"),
            data.get("document_paths"),
            data.get("termination_reason"),
        )
        return record_to_dict(row) if row else None

    async def update_next_visit_date(self, contract_id: str, next_visit_date: str | None) -> None:
        """Update next visit date."""
        await self.conn.execute(
            """
            UPDATE work_order.maintenance_contracts
            SET next_visit_date = $2::date, updated_at = now()
            WHERE id = $1::uuid
            """,
            contract_id,
            next_visit_date,
        )
