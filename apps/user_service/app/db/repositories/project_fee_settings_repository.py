"""Project fee settings persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_SETTINGS_SELECT = """
SELECT
  id::text AS id,
  organization_id::text AS organization_id,
  project_id::text AS project_id,
  currency,
  billing_cycle_type::text AS billing_cycle_type,
  retry_count,
  retry_interval_days,
  reminder_count,
  reminder_interval_days,
  exhausted_retry_action::text AS exhausted_retry_action,
  is_configured,
  configured_at,
  configured_by::text AS configured_by,
  created_at,
  updated_at
FROM project_fee_settings
WHERE organization_id = $1::uuid
  AND project_id = $2::uuid
"""


class ProjectFeeSettingsRepository(BaseRepository):
    """Database operations for public.project_fee_settings."""

    async def get_by_project_id(
        self, *, organization_id: str, project_id: str
    ) -> dict[str, Any] | None:
        """Fetch fee settings for a project."""
        row = await self.db_connection.fetchrow(
            _SETTINGS_SELECT,
            organization_id,
            project_id,
        )
        return dict(row) if row else None

    async def upsert(
        self,
        *,
        organization_id: str,
        project_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Insert or update project fee settings."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO project_fee_settings (
                organization_id,
                project_id,
                currency,
                billing_cycle_type,
                retry_count,
                retry_interval_days,
                reminder_count,
                reminder_interval_days,
                exhausted_retry_action,
                is_configured,
                configured_at,
                configured_by
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3,
                $4::billing_cycle_type,
                $5,
                $6,
                $7,
                $8,
                $9::exhausted_retry_action,
                $10,
                $11,
                $12::uuid
            )
            ON CONFLICT (project_id) DO UPDATE SET
                currency = EXCLUDED.currency,
                billing_cycle_type = EXCLUDED.billing_cycle_type,
                retry_count = EXCLUDED.retry_count,
                retry_interval_days = EXCLUDED.retry_interval_days,
                reminder_count = EXCLUDED.reminder_count,
                reminder_interval_days = EXCLUDED.reminder_interval_days,
                exhausted_retry_action = EXCLUDED.exhausted_retry_action,
                is_configured = EXCLUDED.is_configured,
                configured_at = EXCLUDED.configured_at,
                configured_by = EXCLUDED.configured_by,
                updated_at = now()
            RETURNING
                id::text AS id,
                organization_id::text AS organization_id,
                project_id::text AS project_id,
                currency,
                billing_cycle_type::text AS billing_cycle_type,
                retry_count,
                retry_interval_days,
                reminder_count,
                reminder_interval_days,
                exhausted_retry_action::text AS exhausted_retry_action,
                is_configured,
                configured_at,
                configured_by::text AS configured_by,
                created_at,
                updated_at
            """,
            organization_id,
            project_id,
            data["currency"],
            data["billing_cycle_type"],
            data["retry_count"],
            data["retry_interval_days"],
            data["reminder_count"],
            data["reminder_interval_days"],
            data["exhausted_retry_action"],
            data["is_configured"],
            data.get("configured_at"),
            data.get("configured_by"),
        )
        return dict(row)

    async def list_configured_projects(self, *, organization_id: str) -> list[dict[str, Any]]:
        """List projects with complete fee configuration."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              project_id::text AS project_id,
              currency,
              billing_cycle_type::text AS billing_cycle_type,
              retry_count,
              retry_interval_days,
              reminder_count,
              reminder_interval_days,
              exhausted_retry_action::text AS exhausted_retry_action
            FROM project_fee_settings
            WHERE organization_id = $1::uuid
              AND is_configured = true
            """,
            organization_id,
        )
        return [dict(row) for row in rows]
