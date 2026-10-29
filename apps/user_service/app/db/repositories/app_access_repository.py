"""SQL for app access rules, overrides, states, and unit dues."""

from __future__ import annotations

import inspect
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_RULE_SELECT = """
    SELECT
        id::text AS id,
        organization_id::text AS organization_id,
        project_id::text AS project_id,
        enabled,
        by_amount,
        amount_threshold,
        by_days,
        days_threshold,
        match_mode::text AS match_mode,
        version,
        updated_by::text AS updated_by
    FROM app_access_rules
"""

_STATE_SELECT = """
    SELECT
        project_id::text AS project_id,
        unit_id::text AS unit_id,
        organization_id::text AS organization_id,
        status::text AS status,
        outstanding_amount,
        days_past_due,
        oldest_invoice_date,
        blocked_on,
        blocked_by,
        reason,
        evaluated_at
    FROM app_access_states
"""


class AppAccessRepository(BaseRepository):
    """Persistence for app access control."""

    async def get_rule(self, *, organization_id: str, project_id: str) -> dict[str, Any] | None:
        """Return the project's rule, or None when missing."""
        row = await self.db_connection.fetchrow(
            _RULE_SELECT
            + """
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return dict(row) if row else None

    async def insert_rule(
        self, *, organization_id: str, project_id: str, rule: dict[str, Any]
    ) -> dict[str, Any]:
        """Insert the first rule row and return it."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO app_access_rules (
                organization_id, project_id, enabled, by_amount, amount_threshold,
                by_days, days_threshold, match_mode, updated_by
            )
            VALUES (
                $1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::app_access_match_mode, $9::uuid
            )
            RETURNING id
            """,
            organization_id,
            project_id,
            rule["enabled"],
            rule["by_amount"],
            rule["amount_threshold"],
            rule["by_days"],
            rule["days_threshold"],
            rule["match_mode"],
            rule.get("updated_by"),
        )
        del row
        saved = await self.get_rule(organization_id=organization_id, project_id=project_id)
        assert saved is not None
        return saved

    async def update_rule(
        self,
        *,
        organization_id: str,
        project_id: str,
        expected_version: int,
        rule: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update when version still matches."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE app_access_rules
            SET enabled = $4,
                by_amount = $5,
                amount_threshold = $6,
                by_days = $7,
                days_threshold = $8,
                match_mode = $9::app_access_match_mode,
                updated_by = $10::uuid,
                version = version + 1,
                updated_at = NOW()
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND version = $3
            RETURNING id
            """,
            organization_id,
            project_id,
            expected_version,
            rule["enabled"],
            rule["by_amount"],
            rule["amount_threshold"],
            rule["by_days"],
            rule["days_threshold"],
            rule["match_mode"],
            rule.get("updated_by"),
        )
        if row is None:
            return None
        return await self.get_rule(organization_id=organization_id, project_id=project_id)

    async def get_override(
        self, *, organization_id: str, project_id: str, unit_id: str
    ) -> dict[str, Any] | None:
        """Return the unit override, if any."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
                id::text AS id,
                unit_id::text AS unit_id,
                kind::text AS kind,
                set_by::text AS set_by,
                set_at
            FROM app_access_overrides
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND unit_id = $3::uuid
            """,
            organization_id,
            project_id,
            unit_id,
        )
        return dict(row) if row else None

    async def list_overrides(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """All overrides for the project."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                unit_id::text AS unit_id,
                kind::text AS kind
            FROM app_access_overrides
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def upsert_override(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        kind: str,
        set_by: str | None,
    ) -> None:
        """Insert or replace the unit override."""
        await self.db_connection.execute(
            """
            INSERT INTO app_access_overrides (
                organization_id, project_id, unit_id, kind, set_by, set_at
            )
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::app_access_override_kind, $5::uuid, NOW())
            ON CONFLICT (project_id, unit_id) DO UPDATE
            SET kind = EXCLUDED.kind,
                set_by = EXCLUDED.set_by,
                set_at = NOW()
            """,
            organization_id,
            project_id,
            unit_id,
            kind,
            set_by,
        )

    async def delete_override(self, *, organization_id: str, project_id: str, unit_id: str) -> bool:
        """Remove the override. True when a row was deleted."""
        result = await self.db_connection.execute(
            """
            DELETE FROM app_access_overrides
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND unit_id = $3::uuid
            """,
            organization_id,
            project_id,
            unit_id,
        )
        return result.endswith("1")

    async def get_state(
        self, *, organization_id: str, project_id: str, unit_id: str
    ) -> dict[str, Any] | None:
        """Last evaluation for one unit."""
        result = self.db_connection.fetchrow(
            _STATE_SELECT
            + """
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND unit_id = $3::uuid
            """,
            organization_id,
            project_id,
            unit_id,
        )
        row = await result if inspect.isawaitable(result) else None
        return dict(row) if row else None

    async def list_states(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        """All evaluated units in the project."""
        rows = await self.db_connection.fetch(
            _STATE_SELECT
            + """
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def upsert_state(self, state: dict[str, Any]) -> None:
        """Write the latest evaluation for a unit."""
        await self.db_connection.execute(
            """
            INSERT INTO app_access_states (
                project_id, unit_id, organization_id, status, outstanding_amount,
                days_past_due, oldest_invoice_date, blocked_on, blocked_by, reason,
                evaluated_at
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::app_access_status, $5,
                $6, $7::date, $8::timestamptz, $9, $10, $11::timestamptz
            )
            ON CONFLICT (project_id, unit_id) DO UPDATE
            SET organization_id = EXCLUDED.organization_id,
                status = EXCLUDED.status,
                outstanding_amount = EXCLUDED.outstanding_amount,
                days_past_due = EXCLUDED.days_past_due,
                oldest_invoice_date = EXCLUDED.oldest_invoice_date,
                blocked_on = EXCLUDED.blocked_on,
                blocked_by = EXCLUDED.blocked_by,
                reason = EXCLUDED.reason,
                evaluated_at = EXCLUDED.evaluated_at
            """,
            state["project_id"],
            state["unit_id"],
            state["organization_id"],
            state["status"],
            state["outstanding_amount"],
            state.get("days_past_due"),
            state.get("oldest_invoice_date"),
            state.get("blocked_on"),
            state.get("blocked_by"),
            state.get("reason"),
            state["evaluated_at"],
        )

    async def list_project_ids_to_evaluate(self) -> list[tuple[str, str]]:
        """Org+project pairs that have a rule, override, or state."""
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT organization_id::text AS organization_id,
                            project_id::text AS project_id
            FROM (
                SELECT organization_id, project_id FROM app_access_rules
                UNION
                SELECT organization_id, project_id FROM app_access_overrides
                UNION
                SELECT organization_id, project_id FROM app_access_states
            ) keys
            """
        )
        return [(str(row["organization_id"]), str(row["project_id"])) for row in rows]

    async def unit_project(self, *, organization_id: str, unit_id: str) -> dict[str, Any] | None:
        """Unit code and project for an org-scoped unit."""
        result = self.db_connection.fetchrow(
            """
            SELECT
                id::text AS unit_id,
                project_id::text AS project_id,
                COALESCE(NULLIF(btrim(unit_label), ''), code) AS unit_code
            FROM units
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            """,
            organization_id,
            unit_id,
        )
        row = await result if inspect.isawaitable(result) else None
        return dict(row) if row else None

    async def list_unit_dues(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str | None,
        as_of: date,
    ) -> list[dict[str, Any]]:
        """Outstanding, oldest invoice date, and days past due per unit."""
        args: list[Any] = [organization_id, project_id, as_of]
        unit_filter = ""
        if unit_id is not None:
            args.append(unit_id)
            unit_filter = f"AND i.unit_id = ${len(args)}::uuid"
        rows = await self.db_connection.fetch(
            f"""
            WITH open_invoices AS (
                SELECT
                    i.unit_id,
                    i.invoice_date,
                    i.due_date,
                    i.total_amount - COALESCE((
                        SELECT SUM(p.amount)
                        FROM fee_invoice_payments p
                        WHERE p.invoice_id = i.id
                    ), 0) AS invoice_outstanding
                FROM fee_invoices i
                WHERE i.organization_id = $1::uuid
                  AND i.project_id = $2::uuid
                  AND i.status IN ('issued', 'partial')
                  {unit_filter}
            ),
            positive AS (
                SELECT *
                FROM open_invoices
                WHERE invoice_outstanding > 0
            ),
            dues AS (
                SELECT
                    unit_id,
                    SUM(invoice_outstanding) AS invoice_outstanding,
                    MIN(invoice_date) AS oldest_invoice_date,
                    MAX(
                        CASE
                            WHEN due_date < $3::date THEN ($3::date - due_date)
                            ELSE 0
                        END
                    ) AS days_past_due
                FROM positive
                GROUP BY unit_id
            )
            SELECT
                d.unit_id::text AS unit_id,
                GREATEST(
                    0,
                    d.invoice_outstanding - COALESCE(c.credit, 0)
                ) AS outstanding_amount,
                d.oldest_invoice_date,
                d.days_past_due
            FROM dues d
            LEFT JOIN (
                SELECT unit_id, SUM(amount) AS credit
                FROM fee_unit_credits
                WHERE organization_id = $1::uuid
                  AND project_id = $2::uuid
                GROUP BY unit_id
            ) c ON c.unit_id = d.unit_id
            """,
            *args,
        )
        return [dict(row) for row in rows]

    async def list_eval_unit_ids(self, *, organization_id: str, project_id: str) -> list[str]:
        """Units with open invoices, an override, or a state row."""
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT unit_id::text AS unit_id
            FROM (
                SELECT unit_id
                FROM fee_invoices
                WHERE organization_id = $1::uuid
                  AND project_id = $2::uuid
                  AND status IN ('issued', 'partial')
                UNION
                SELECT unit_id
                FROM app_access_overrides
                WHERE organization_id = $1::uuid
                  AND project_id = $2::uuid
                UNION
                SELECT unit_id
                FROM app_access_states
                WHERE organization_id = $1::uuid
                  AND project_id = $2::uuid
            ) units
            """,
            organization_id,
            project_id,
        )
        return [str(row["unit_id"]) for row in rows]

    async def list_units_page(
        self,
        *,
        organization_id: str,
        project_id: str,
        q: str | None,
        status: str | None,
        limit: int,
        offset: int,
        unit_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Customers-tab rows from persisted states."""
        args: list[Any] = [organization_id, project_id]
        filters = [
            "s.organization_id = $1::uuid",
            "s.project_id = $2::uuid",
        ]
        if unit_id:
            args.append(unit_id)
            filters.append(f"s.unit_id = ${len(args)}::uuid")
        if status:
            args.append(status)
            filters.append(f"s.status = ${len(args)}::app_access_status")
        if q:
            args.append(f"%{q.strip()}%")
            q_idx = len(args)
            filters.append(
                f"""(
                    COALESCE(NULLIF(btrim(u.unit_label), ''), u.code) ILIKE ${q_idx}
                    OR COALESCE(occ.display_name, '') ILIKE ${q_idx}
                )"""
            )
        where_sql = " AND ".join(filters)
        count_row = await self.db_connection.fetchrow(
            f"""
            SELECT COUNT(*) AS total
            FROM app_access_states s
            JOIN units u ON u.id = s.unit_id
            LEFT JOIN LATERAL (
                SELECT CONCAT_WS(
                    ' ',
                    NULLIF(btrim(c.first_name), ''),
                    NULLIF(btrim(c.last_name), '')
                ) AS display_name
                FROM contact_roles cr
                JOIN contacts c ON c.id = cr.contact_id
                WHERE cr.organization_id = s.organization_id
                  AND cr.unit_id = s.unit_id
                  AND cr.status = 'active'
                  AND cr.role_type IN ('Owner', 'Tenant', 'Family')
                ORDER BY CASE cr.role_type
                    WHEN 'Owner' THEN 0
                    WHEN 'Tenant' THEN 1
                    ELSE 2
                END, cr.started_at
                LIMIT 1
            ) occ ON true
            WHERE {where_sql}
            """,
            *args,
        )
        total = int(count_row["total"]) if count_row else 0
        args.extend([limit, offset])
        rows = await self.db_connection.fetch(
            f"""
            SELECT
                s.unit_id::text AS unit_id,
                COALESCE(NULLIF(btrim(u.unit_label), ''), u.code) AS unit_code,
                COALESCE(NULLIF(btrim(occ.display_name), ''), '—') AS display_name,
                s.outstanding_amount,
                s.oldest_invoice_date,
                s.days_past_due,
                s.status::text AS status,
                s.blocked_on,
                s.blocked_by,
                s.reason,
                ov.kind::text AS override_kind,
                s.evaluated_at
            FROM app_access_states s
            JOIN units u ON u.id = s.unit_id
            LEFT JOIN app_access_overrides ov
                ON ov.project_id = s.project_id AND ov.unit_id = s.unit_id
            LEFT JOIN LATERAL (
                SELECT CONCAT_WS(
                    ' ',
                    NULLIF(btrim(c.first_name), ''),
                    NULLIF(btrim(c.last_name), '')
                ) AS display_name
                FROM contact_roles cr
                JOIN contacts c ON c.id = cr.contact_id
                WHERE cr.organization_id = s.organization_id
                  AND cr.unit_id = s.unit_id
                  AND cr.status = 'active'
                  AND cr.role_type IN ('Owner', 'Tenant', 'Family')
                ORDER BY CASE cr.role_type
                    WHEN 'Owner' THEN 0
                    WHEN 'Tenant' THEN 1
                    ELSE 2
                END, cr.started_at
                LIMIT 1
            ) occ ON true
            WHERE {where_sql}
            ORDER BY s.status, u.code
            LIMIT ${len(args) - 1} OFFSET ${len(args)}
            """,
            *args,
        )
        return [dict(row) for row in rows], total

    async def list_contact_active_units(
        self, *, organization_id: str, contact_id: str
    ) -> list[dict[str, Any]]:
        """Active units for a resident contact."""
        result = self.db_connection.fetch(
            """
            SELECT
                cu.unit_id::text AS unit_id,
                cu.project_id::text AS project_id,
                COALESCE(NULLIF(btrim(u.unit_label), ''), u.code) AS unit_code,
                cu.is_default_login
            FROM contact_units cu
            JOIN units u ON u.id = cu.unit_id
            WHERE cu.organization_id = $1::uuid
              AND cu.contact_id = $2::uuid
              AND cu.status = 'active'
            ORDER BY cu.is_default_login DESC, cu.sort_order, cu.created_at
            """,
            organization_id,
            contact_id,
        )
        rows = await result if inspect.isawaitable(result) else []
        return [dict(row) for row in rows]

    async def list_contact_units_in_project(
        self, *, organization_id: str, contact_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """Active units for a contact in one project."""
        result = self.db_connection.fetch(
            """
            SELECT
                cu.unit_id::text AS unit_id,
                COALESCE(NULLIF(btrim(u.unit_label), ''), u.code) AS unit_code,
                cu.is_default_login
            FROM contact_units cu
            JOIN units u ON u.id = cu.unit_id
            WHERE cu.organization_id = $1::uuid
              AND cu.contact_id = $2::uuid
              AND cu.project_id = $3::uuid
              AND cu.status = 'active'
            ORDER BY cu.is_default_login DESC, cu.sort_order, cu.created_at
            """,
            organization_id,
            contact_id,
            project_id,
        )
        rows = await result if inspect.isawaitable(result) else []
        return [dict(row) for row in rows]


def as_decimal(value: Any) -> Decimal:
    """Money from a DB numeric."""
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value))


def as_iso(value: datetime | date | None) -> str | None:
    """ISO text for a date or timestamp."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value.isoformat()
