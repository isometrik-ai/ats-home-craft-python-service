"""Persistence for fee heads, property scopes, and project finance settings."""

from __future__ import annotations

import json
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.utils.common_utils import serialize_jsonb_param

_JSONB_COLUMNS = frozenset({"charge", "tax", "late_fee"})

_HEAD_SELECT = """
    SELECT
        id,
        organization_id,
        project_id,
        kind::text AS kind,
        category::text AS category,
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
        meter_read_day,
        charge,
        tax,
        late_fee,
        version
    FROM fee_heads
"""

_KIND_ORDER_SQL = """
    CASE kind
        WHEN 'maintenance' THEN 1
        WHEN 'electricity' THEN 2
        ELSE 3
    END
"""

_SCOPE_ORDER_SQL = """
    CASE property_type
        WHEN 'residential' THEN 1
        WHEN 'plots' THEN 2
        ELSE 3
    END
"""


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    for column in _JSONB_COLUMNS:
        value = row.get(column)
        if isinstance(value, str):
            row[column] = json.loads(value)
    return row


class FeeConfigurationRepository(BaseRepository):
    """SQL for fee configuration. Version updates are compare-and-swap."""

    async def list_heads(self, *, organization_id: str, project_id: str) -> list[dict[str, Any]]:
        """Return the project's fee heads in kind order, without scopes."""
        rows = await self.db_connection.fetch(
            f"""
            {_HEAD_SELECT}
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            ORDER BY {_KIND_ORDER_SQL}
            """,
            organization_id,
            project_id,
        )
        return [_decode_row(dict(row)) for row in rows]

    async def get_head(
        self, *, organization_id: str, project_id: str, fee_head_id: str
    ) -> dict[str, Any] | None:
        """Return one fee head, or None when it is not in this project."""
        row = await self.db_connection.fetchrow(
            f"""
            {_HEAD_SELECT}
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND id = $3::uuid
            """,
            organization_id,
            project_id,
            fee_head_id,
        )
        return _decode_row(dict(row)) if row else None

    async def get_head_by_kind(
        self, *, organization_id: str, project_id: str, kind: str
    ) -> dict[str, Any] | None:
        """Return the project's fee head of this kind, or None."""
        row = await self.db_connection.fetchrow(
            f"""
            {_HEAD_SELECT}
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND kind = $3::fee_head_kind
            """,
            organization_id,
            project_id,
            kind,
        )
        return _decode_row(dict(row)) if row else None

    async def insert_head(
        self,
        *,
        organization_id: str,
        project_id: str,
        kind: str,
        category: str,
        head: dict[str, Any],
    ) -> str:
        """Insert one fee head and return its id."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO fee_heads (
                organization_id,
                project_id,
                kind,
                category,
                name,
                line_description,
                status,
                frequency,
                billing_cycle,
                cycle_anchor_month,
                fee_start_rule,
                fee_start_date,
                due_within_days,
                invoice_day,
                meter_read_day,
                charge,
                tax,
                late_fee,
                updated_by
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3::fee_head_kind,
                $4::fee_head_category,
                $5,
                $6,
                $7::fee_head_status,
                $8::fee_frequency,
                $9::fee_billing_cycle,
                $10,
                $11::fee_start_rule,
                $12::date,
                $13,
                $14,
                $15,
                $16::jsonb,
                $17::jsonb,
                $18::jsonb,
                $19::uuid
            )
            RETURNING id
            """,
            organization_id,
            project_id,
            kind,
            category,
            head["name"],
            head["line_description"],
            head["status"],
            head["frequency"],
            head["billing_cycle"],
            head["cycle_anchor_month"],
            head["fee_start_rule"],
            head["fee_start_date"],
            head["due_within_days"],
            head["invoice_day"],
            head["meter_read_day"],
            serialize_jsonb_param("charge", head["charge"], _JSONB_COLUMNS),
            serialize_jsonb_param("tax", head["tax"], _JSONB_COLUMNS),
            serialize_jsonb_param("late_fee", head["late_fee"], _JSONB_COLUMNS),
            head.get("updated_by"),
        )
        return str(row["id"])

    async def insert_scopes(
        self,
        *,
        fee_head_id: str,
        organization_id: str,
        scopes: list[dict[str, Any]],
    ) -> None:
        """Insert the three property-scope rows for a new fee head."""
        for scope in scopes:
            await self.db_connection.execute(
                """
                INSERT INTO fee_head_property_scopes (
                    fee_head_id,
                    organization_id,
                    property_type,
                    enabled,
                    rate_per_sqft,
                    minimum_amount
                )
                VALUES ($1::uuid, $2::uuid, $3::property_type, $4, $5, $6)
                """,
                fee_head_id,
                organization_id,
                scope["property_type"],
                scope["enabled"],
                scope.get("rate_per_sqft"),
                scope.get("minimum_amount"),
            )

    async def list_scopes(self, *, fee_head_ids: list[str]) -> list[dict[str, Any]]:
        """Return scopes for the given heads, Apartments then Plots then Commercial."""
        if not fee_head_ids:
            return []
        rows = await self.db_connection.fetch(
            f"""
            SELECT
                fee_head_id,
                property_type::text AS property_type,
                enabled,
                rate_per_sqft,
                minimum_amount
            FROM fee_head_property_scopes
            WHERE fee_head_id = ANY($1::uuid[])
            ORDER BY fee_head_id, {_SCOPE_ORDER_SQL}
            """,
            fee_head_ids,
        )
        return [dict(row) for row in rows]

    async def update_head(
        self,
        *,
        organization_id: str,
        project_id: str,
        fee_head_id: str,
        expected_version: int,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Apply the update when version still matches. None means a stale write."""
        set_parts: list[str] = []
        params: list[Any] = []
        enum_casts = {
            "status": "::fee_head_status",
            "frequency": "::fee_frequency",
            "billing_cycle": "::fee_billing_cycle",
            "fee_start_rule": "::fee_start_rule",
            "fee_start_date": "::date",
        }
        for key, value in update_data.items():
            params.append(serialize_jsonb_param(key, value, _JSONB_COLUMNS))
            if key in _JSONB_COLUMNS:
                cast = "::jsonb"
            else:
                cast = enum_casts.get(key, "")
            set_parts.append(f"{key} = ${len(params)}{cast}")
        set_parts.extend(["version = version + 1", "updated_at = NOW()"])
        params.extend([fee_head_id, organization_id, project_id, expected_version])
        n = len(params)
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE fee_heads
            SET {", ".join(set_parts)}
            WHERE id = ${n - 3}::uuid
              AND organization_id = ${n - 2}::uuid
              AND project_id = ${n - 1}::uuid
              AND version = ${n}
            RETURNING id
            """,
            *params,
        )
        return dict(row) if row else None

    async def replace_scopes(self, *, fee_head_id: str, scopes: list[dict[str, Any]]) -> None:
        """Update the three scope rows in place. Rows are never inserted or deleted."""
        for scope in scopes:
            await self.db_connection.execute(
                """
                UPDATE fee_head_property_scopes
                SET enabled = $3,
                    rate_per_sqft = $4,
                    minimum_amount = $5
                WHERE fee_head_id = $1::uuid
                  AND property_type = $2::property_type
                """,
                fee_head_id,
                scope["property_type"],
                scope["enabled"],
                scope.get("rate_per_sqft"),
                scope.get("minimum_amount"),
            )

    async def get_settings(self, *, organization_id: str, project_id: str) -> dict[str, Any] | None:
        """Return the project's dunning settings, or None when the row is missing."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
                payment_retry_count,
                payment_retry_interval_days,
                pre_due_reminder_count,
                pre_due_reminder_interval_days,
                version
            FROM finance_settings
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return dict(row) if row else None

    async def update_settings(
        self,
        *,
        organization_id: str,
        project_id: str,
        expected_version: int,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update dunning numbers when version still matches."""
        set_parts: list[str] = []
        params: list[Any] = []
        for key, value in update_data.items():
            params.append(value)
            set_parts.append(f"{key} = ${len(params)}")
        set_parts.extend(["version = version + 1", "updated_at = NOW()"])
        params.extend([organization_id, project_id, expected_version])
        n = len(params)
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE finance_settings
            SET {", ".join(set_parts)}
            WHERE organization_id = ${n - 2}::uuid
              AND project_id = ${n - 1}::uuid
              AND version = ${n}
            RETURNING id
            """,
            *params,
        )
        return dict(row) if row else None

    async def insert_settings(
        self,
        *,
        organization_id: str,
        project_id: str,
        settings: dict[str, Any],
    ) -> None:
        """Insert the project's finance settings row."""
        await self.db_connection.execute(
            """
            INSERT INTO finance_settings (
                organization_id,
                project_id,
                payment_retry_count,
                payment_retry_interval_days,
                pre_due_reminder_count,
                pre_due_reminder_interval_days,
                updated_by
            )
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::uuid)
            """,
            organization_id,
            project_id,
            settings["payment_retry_count"],
            settings["payment_retry_interval_days"],
            settings["pre_due_reminder_count"],
            settings["pre_due_reminder_interval_days"],
            settings.get("updated_by"),
        )
