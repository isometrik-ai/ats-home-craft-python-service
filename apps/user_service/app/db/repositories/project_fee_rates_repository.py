"""Project fee rates persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_RATES_SELECT = """
SELECT
  id::text AS id,
  organization_id::text AS organization_id,
  project_id::text AS project_id,
  unit_config_kind::text AS unit_config_kind,
  rate_amount_minor_per_unit,
  measurement_unit::text AS measurement_unit,
  billing_frequency::text AS billing_frequency,
  fee_start_trigger::text AS fee_start_trigger,
  start_offset_days,
  minimum_fee_minor,
  created_at,
  updated_at
FROM project_fee_rates
WHERE organization_id = $1::uuid
  AND project_id = $2::uuid
ORDER BY unit_config_kind
"""


class ProjectFeeRatesRepository(BaseRepository):
    """Database operations for public.project_fee_rates."""

    async def list_by_project_id(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """List all rate rows for a project."""
        rows = await self.db_connection.fetch(_RATES_SELECT, organization_id, project_id)
        return [dict(row) for row in rows]

    async def upsert_batch(
        self,
        *,
        organization_id: str,
        project_id: str,
        rates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Upsert multiple rate rows."""
        if not rates:
            return []
        results: list[dict[str, Any]] = []
        for rate in rates:
            row = await self.db_connection.fetchrow(
                """
                INSERT INTO project_fee_rates (
                    organization_id,
                    project_id,
                    unit_config_kind,
                    rate_amount_minor_per_unit,
                    measurement_unit,
                    billing_frequency,
                    fee_start_trigger,
                    start_offset_days,
                    minimum_fee_minor
                )
                VALUES (
                    $1::uuid,
                    $2::uuid,
                    $3::unit_config_kind,
                    $4,
                    $5::measurement_unit,
                    $6::billing_frequency,
                    $7::fee_start_trigger,
                    $8,
                    $9
                )
                ON CONFLICT (project_id, unit_config_kind) DO UPDATE SET
                    rate_amount_minor_per_unit = EXCLUDED.rate_amount_minor_per_unit,
                    measurement_unit = EXCLUDED.measurement_unit,
                    billing_frequency = EXCLUDED.billing_frequency,
                    fee_start_trigger = EXCLUDED.fee_start_trigger,
                    start_offset_days = EXCLUDED.start_offset_days,
                    minimum_fee_minor = EXCLUDED.minimum_fee_minor,
                    updated_at = now()
                RETURNING
                    id::text AS id,
                    organization_id::text AS organization_id,
                    project_id::text AS project_id,
                    unit_config_kind::text AS unit_config_kind,
                    rate_amount_minor_per_unit,
                    measurement_unit::text AS measurement_unit,
                    billing_frequency::text AS billing_frequency,
                    fee_start_trigger::text AS fee_start_trigger,
                    start_offset_days,
                    minimum_fee_minor,
                    created_at,
                    updated_at
                """,
                organization_id,
                project_id,
                rate["unit_config_kind"],
                rate["rate_amount_minor_per_unit"],
                rate["measurement_unit"],
                rate["billing_frequency"],
                rate["fee_start_trigger"],
                rate.get("start_offset_days"),
                rate["minimum_fee_minor"],
            )
            results.append(dict(row))
        return results

    async def delete_kinds_not_in(
        self,
        *,
        organization_id: str,
        project_id: str,
        kinds: list[str],
    ) -> None:
        """Remove rate rows whose kind is no longer applicable."""
        if kinds:
            await self.db_connection.execute(
                """
                DELETE FROM project_fee_rates
                WHERE organization_id = $1::uuid
                  AND project_id = $2::uuid
                  AND NOT (unit_config_kind = ANY($3::unit_config_kind[]))
                """,
                organization_id,
                project_id,
                kinds,
            )
        else:
            await self.db_connection.execute(
                """
                DELETE FROM project_fee_rates
                WHERE organization_id = $1::uuid
                  AND project_id = $2::uuid
                """,
                organization_id,
                project_id,
            )
