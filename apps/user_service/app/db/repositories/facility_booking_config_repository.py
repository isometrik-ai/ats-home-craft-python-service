"""Persistence for facility booking configs and per-project booking settings."""

from __future__ import annotations

import json
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

CONFIG_JSONB_COLUMNS: frozenset[str] = frozenset(
    {"default_hours", "pricing", "policies", "setup", "policies_document"}
)

_CONFIG_SELECT = """
    SELECT
        c.*,
        f.name AS facility_name,
        f.facility_type,
        f.booking_archetype::text AS archetype,
        f.is_bookable,
        f.status::text AS facility_status,
        f.active AS facility_active,
        f.tower_id AS facility_tower_id,
        f.location_notes AS facility_location_notes,
        f.sort_order AS facility_sort_order
    FROM facility_booking_configs c
    INNER JOIN facilities f ON f.id = c.facility_id
"""


def decode_jsonb(row: dict[str, Any], columns: frozenset[str]) -> dict[str, Any]:
    """asyncpg returns jsonb as text (no codec registered); decode in place."""
    for col in columns:
        value = row.get(col)
        if isinstance(value, str):
            row[col] = json.loads(value)
    return row


class FacilityBookingConfigRepository(BaseRepository):
    """facility_booking_configs + project_booking_settings."""

    async def insert_config(self, data: dict[str, Any]) -> dict[str, Any]:
        rows = await self.bulk_insert_returning(
            table="facility_booking_configs",
            required_columns=[
                "organization_id",
                "project_id",
                "facility_id",
                "default_hours",
                "pricing",
                "policies",
                "setup",
            ],
            optional_columns=[
                "description",
                "slot_minutes",
                "accepting_bookings",
                "policies_document",
                "created_by_user_id",
                "updated_by_user_id",
            ],
            rows=[data],
            jsonb_columns=CONFIG_JSONB_COLUMNS,
        )
        return decode_jsonb(rows[0], CONFIG_JSONB_COLUMNS)

    async def get_config(
        self, *, organization_id: str, project_id: str, facility_id: str
    ) -> dict[str, Any] | None:
        row = await self.db_connection.fetchrow(
            f"""
            {_CONFIG_SELECT}
            WHERE c.organization_id = $1::uuid
              AND c.project_id = $2::uuid
              AND c.facility_id = $3::uuid
            """,
            organization_id,
            project_id,
            facility_id,
        )
        return decode_jsonb(dict(row), CONFIG_JSONB_COLUMNS) if row else None

    async def list_configs(
        self,
        *,
        organization_id: str,
        project_id: str,
        bookable_only: bool = True,
        resident_visible_only: bool = False,
    ) -> list[dict[str, Any]]:
        conditions = ["c.organization_id = $1::uuid", "c.project_id = $2::uuid"]
        if bookable_only:
            conditions.append("f.is_bookable")
        if resident_visible_only:
            conditions.append("f.active AND f.status = 'active'")
        rows = await self.db_connection.fetch(
            f"""
            {_CONFIG_SELECT}
            WHERE {" AND ".join(conditions)}
            ORDER BY f.sort_order, f.name
            """,
            organization_id,
            project_id,
        )
        return [decode_jsonb(dict(r), CONFIG_JSONB_COLUMNS) for r in rows]

    async def update_config(
        self,
        *,
        organization_id: str,
        facility_id: str,
        expected_version: int,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Apply the update when ``version`` still matches; returns None on a stale write."""
        set_parts: list[str] = []
        params: list[Any] = []
        for key, value in update_data.items():
            params.append(
                json.dumps(value) if key in CONFIG_JSONB_COLUMNS and value is not None else value
            )
            cast = "::jsonb" if key in CONFIG_JSONB_COLUMNS else ""
            set_parts.append(f"{key} = ${len(params)}{cast}")
        set_parts.extend(["version = version + 1", "updated_at = NOW()"])
        params.extend([facility_id, organization_id, expected_version])
        n = len(params)
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE facility_booking_configs SET {", ".join(set_parts)}
            WHERE facility_id = ${n - 2}::uuid
              AND organization_id = ${n - 1}::uuid
              AND version = ${n}
            RETURNING id
            """,
            *params,
        )
        return dict(row) if row else None

    async def get_settings(self, *, organization_id: str, project_id: str) -> dict[str, Any] | None:
        row = await self.db_connection.fetchrow(
            """
            SELECT * FROM project_booking_settings
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return dict(row) if row else None

    async def upsert_settings(
        self,
        *,
        organization_id: str,
        project_id: str,
        timezone: str,
        currency_code: str,
        user_id: str | None,
        invoice_frequency: str | None = None,
        wallet_enabled: bool | None = None,
        online_enabled: bool | None = None,
        cash_enabled: bool | None = None,
        wallet_credit_limit: int | None = None,
    ) -> dict[str, Any]:
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO project_booking_settings (
                organization_id, project_id, timezone, currency_code, updated_by_user_id,
                invoice_frequency, wallet_enabled, online_enabled, cash_enabled, wallet_credit_limit
            )
            VALUES (
                $1::uuid, $2::uuid, $3, $4, $5::uuid,
                COALESCE($6::facility_booking_invoice_frequency, 'monthly'),
                COALESCE($7, true),
                COALESCE($8, true),
                COALESCE($9, true),
                COALESCE($10, 10000)
            )
            ON CONFLICT (project_id) DO UPDATE SET
                timezone = EXCLUDED.timezone,
                currency_code = EXCLUDED.currency_code,
                updated_by_user_id = EXCLUDED.updated_by_user_id,
                invoice_frequency = COALESCE(
                    $6::facility_booking_invoice_frequency,
                    project_booking_settings.invoice_frequency
                ),
                wallet_enabled = COALESCE($7, project_booking_settings.wallet_enabled),
                online_enabled = COALESCE($8, project_booking_settings.online_enabled),
                cash_enabled = COALESCE($9, project_booking_settings.cash_enabled),
                wallet_credit_limit = COALESCE($10, project_booking_settings.wallet_credit_limit),
                updated_at = NOW()
            RETURNING *
            """,
            organization_id,
            project_id,
            timezone,
            currency_code,
            user_id,
            invoice_frequency,
            wallet_enabled,
            online_enabled,
            cash_enabled,
            wallet_credit_limit,
        )
        return dict(row)

    async def get_organization_timezone(self, organization_id: str) -> str | None:
        value = await self.db_connection.fetchval(
            "SELECT timezone FROM organizations WHERE id = $1::uuid", organization_id
        )
        return str(value) if value else None
