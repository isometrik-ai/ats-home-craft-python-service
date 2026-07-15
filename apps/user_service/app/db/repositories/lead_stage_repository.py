"""Lead Stage Database Repository Module - AsyncPG Implementation.

This module contains lead stage-related database operations using asyncpg.
"""

from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import DEFAULT_ORGANIZATION_LEAD_STAGES
from apps.user_service.app.utils.common_utils import enum_member_title_label


class LeadStageRepository:
    """Persistence for the `lead_stages` table (CRUD and sort-order maintenance).

    DB: ``uq_lsd_sort_order`` is ``UNIQUE (organization_id, sort_order) DEFERRABLE INITIALLY
    DEFERRED`` so reordering can run as multiple UPDATEs in one transaction; uniqueness is
    enforced at commit, not after each statement.
    """

    TABLE_NAME = "lead_stages"

    # Prevent mass-assignment by only allowing known, safe updatable columns.
    # Keep this in sync with what the service layer actually sends.
    UPDATABLE_FIELDS: set[str] = {
        "stage_name",
        "stage_key",
        "description",
        "color",
        "sort_order",
    }

    STAGE_COLUMNS = """
        id,
        stage_name,
        stage_key,
        description,
        color,
        sort_order,
        created_at,
        updated_at
    """

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection."""
        self.db_connection = db_connection

    @classmethod
    def _stage_columns_expr(cls) -> str:
        """Single-line column list for SQL fragments."""
        return cls.STAGE_COLUMNS.strip().replace("\n", " ")

    @classmethod
    def _sql_select_stages(cls, where: str, *, order_by: str | None = None) -> str:
        """Build SELECT of stage columns from the lead_stages table."""
        query = f"SELECT {cls._stage_columns_expr()}\nFROM {cls.TABLE_NAME}\nWHERE {where}"
        if order_by:
            query = f"{query}\n{order_by}"
        return query

    async def adjust_sort_orders(
        self,
        organization_id: str,
        *,
        min_sort_order: int,
        max_sort_order: int | None,
        delta: int,
    ) -> None:
        """Add `delta` to sort_order for matching rows in this organization.

        Rows match when ``sort_order >= min_sort_order`` and, if ``max_sort_order`` is set,
        ``sort_order <= max_sort_order``. Pass ``max_sort_order=None`` for no upper bound.

        Relies on deferred ``uq_lsd_sort_order`` when combined with other writes in the same txn.
        """
        query = f"""
            UPDATE {self.TABLE_NAME}
            SET sort_order = sort_order + $4::int
            WHERE organization_id = $1
              AND sort_order >= $2::int
              AND ($3::int IS NULL OR sort_order <= $3::int)
        """
        await self.db_connection.execute(
            query, organization_id, min_sort_order, max_sort_order, delta
        )

    async def summarize_organization_for_new_stage(
        self,
        organization_id: str,
        stage_key: str,
    ) -> dict[str, Any]:
        """Return how many stages exist, current max sort_order, and if `stage_key` is taken."""
        table = self.TABLE_NAME
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
                (SELECT COUNT(*)::int FROM {table} WHERE organization_id = $1) AS total_stages,
                (SELECT COALESCE(MAX(sort_order), 0)::int FROM {table}
                 WHERE organization_id = $1) AS max_sort_order,
                EXISTS(
                    SELECT 1 FROM {table}
                    WHERE organization_id = $1 AND stage_key = $2
                ) AS stage_key_exists
            """,
            organization_id,
            stage_key,
        )
        return dict(row)

    async def create_stage(self, row: dict[str, Any]) -> dict[str, Any]:
        """Insert a new lead stage and return the persisted row."""
        cols = self._stage_columns_expr()
        query = f"""
            INSERT INTO {self.TABLE_NAME} (
                organization_id,
                stage_name,
                stage_key,
                description,
                color,
                sort_order
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING {cols}
        """
        row = await self.db_connection.fetchrow(
            query,
            row["organization_id"],
            row["stage_name"],
            row["stage_key"],
            row.get("description"),
            row.get("color"),
            row["sort_order"],
        )
        return dict(row)

    async def bulk_insert_default_stages_for_organization(self, organization_id: str) -> None:
        """Insert the standard lead-stage pipeline for a new org in one statement.

        Uses a single ``INSERT … SELECT unnest(...)`` so sort_order uniqueness is
        satisfied without deferred-constraint tricks.
        """
        if not DEFAULT_ORGANIZATION_LEAD_STAGES:
            return

        stage_names = [
            enum_member_title_label(status) for status, _, _ in DEFAULT_ORGANIZATION_LEAD_STAGES
        ]
        stage_keys = [status.value for status, _, _ in DEFAULT_ORGANIZATION_LEAD_STAGES]
        descriptions = [text for _, _, text in DEFAULT_ORGANIZATION_LEAD_STAGES]
        colors = [color.value for _, color, _ in DEFAULT_ORGANIZATION_LEAD_STAGES]
        sort_orders = list(range(1, len(DEFAULT_ORGANIZATION_LEAD_STAGES) + 1))
        table = self.TABLE_NAME
        query = f"""
            INSERT INTO {table} (
                organization_id,
                stage_name,
                stage_key,
                description,
                color,
                sort_order
            )
            SELECT
                $1::uuid,
                stage_name,
                stage_key,
                description,
                color,
                sort_order
            FROM unnest(
                $2::text[],
                $3::text[],
                $4::text[],
                $5::text[],
                $6::int[]
            ) AS t(stage_name, stage_key, description, color, sort_order)
        """
        await self.db_connection.execute(
            query,
            organization_id,
            stage_names,
            stage_keys,
            descriptions,
            colors,
            sort_orders,
        )

    async def list_stages_by_organization(self, organization_id: str) -> list[dict[str, Any]]:
        """Return every lead stage for the organization, ordered by sort_order."""
        query = self._sql_select_stages(
            "organization_id = $1",
            order_by="ORDER BY sort_order ASC",
        )
        rows = await self.db_connection.fetch(query, organization_id)
        return [dict(row) for row in rows]

    async def get_stages_by_ids(
        self,
        *,
        organization_id: str,
        stage_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Fetch multiple stages by id for one organization."""
        if not stage_ids:
            return []
        cols = self._stage_columns_expr()
        query = f"""
            SELECT {cols}
            FROM {self.TABLE_NAME}
            WHERE organization_id = $1
              AND id = ANY($2::uuid[])
        """
        rows = await self.db_connection.fetch(query, organization_id, stage_ids)
        return [dict(row) for row in rows]

    async def get_stage_by_id(self, organization_id: str, stage_id: str) -> dict[str, Any] | None:
        """Return one lead stage by id, scoped to the organization."""
        query = self._sql_select_stages(
            "organization_id = $1\n  AND id = $2::uuid\nLIMIT 1",
        )
        row = await self.db_connection.fetchrow(query, organization_id, stage_id)
        return dict(row) if row else None

    async def get_stage_by_id_with_max_sort_order(
        self, organization_id: str, stage_id: str
    ) -> dict[str, Any] | None:
        """Return one stage row plus organization-level max sort_order in a single query."""
        cols = self._stage_columns_expr()
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
                {cols},
                COALESCE(max_stage.sort_order, 0)::int AS max_sort_order
            FROM {self.TABLE_NAME} s
            LEFT JOIN LATERAL (
                SELECT sort_order
                FROM {self.TABLE_NAME}
                WHERE organization_id = $1
                ORDER BY sort_order DESC
                LIMIT 1
            ) max_stage ON TRUE
            WHERE s.organization_id = $1
            AND s.id = $2::uuid
            """,
            organization_id,
            stage_id,
        )
        return dict(row) if row else None

    async def get_stage_by_id_with_organization_metrics(
        self,
        organization_id: str,
        stage_id: str,
        proposed_stage_key: str | None,
    ) -> dict | None:
        """Return the stage row plus org-wide counts (totals, key clash, other initial/final flags).

        `proposed_stage_key` is compared to other rows to populate `key_conflict_count`.
        Returns None if the stage does not exist in that organization.

        Returned keys:
            Stage columns — id, stage_name, stage_key, description, color,
                sort_order, created_at, updated_at
            Extra columns — total_stages, key_conflict_count
        """
        table = self.TABLE_NAME
        row = await self.db_connection.fetchrow(
            f"""
            WITH org_stats AS (
                SELECT
                    COUNT(*)::int AS total_stages,
                    COUNT(*) FILTER (
                        WHERE stage_key = $3 AND id != $2::uuid
                    ) AS key_conflict_count
                FROM {table}
                WHERE organization_id = $1
            )
            SELECT s.*, stats.*
            FROM {table} s
            CROSS JOIN org_stats stats
            WHERE s.id = $2::uuid
              AND s.organization_id = $1
            """,
            organization_id,
            stage_id,
            proposed_stage_key or "",
        )
        return dict(row) if row else None

    async def update_stage(
        self,
        organization_id: str,
        stage_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch allowed columns and return the updated row (or None if no matching row)."""
        filtered_update_data = {
            field: value for field, value in update_data.items() if field in self.UPDATABLE_FIELDS
        }
        if not filtered_update_data:
            return await self.get_stage_by_id(organization_id, stage_id)

        set_clauses: list[str] = []
        values: list[Any] = [organization_id, stage_id]
        param_index = 3
        for field, value in filtered_update_data.items():
            set_clauses.append(f"{field} = ${param_index}")
            values.append(value)
            param_index += 1

        cols = self._stage_columns_expr()
        query = f"""
            UPDATE {self.TABLE_NAME}
            SET {", ".join(set_clauses)}
            WHERE organization_id = $1
              AND id = $2::uuid
            RETURNING {cols}
        """
        row = await self.db_connection.fetchrow(query, *values)
        return dict(row) if row else None

    async def delete_stage(self, organization_id: str, stage_id: str) -> dict[str, Any] | None:
        """Hard-delete one stage scoped to the organization; return the removed row if any."""
        cols = self._stage_columns_expr()
        query = f"""
            DELETE FROM {self.TABLE_NAME}
            WHERE organization_id = $1
              AND id = $2::uuid
            RETURNING {cols}
        """
        row = await self.db_connection.fetchrow(query, organization_id, stage_id)
        return dict(row) if row else None
