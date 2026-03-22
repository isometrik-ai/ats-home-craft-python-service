"""Lead persistence (public.leads) — asyncpg."""

from typing import Any

import asyncpg

from apps.user_service.app.utils.common_utils import serialize_jsonb_param

# SQL fragments (shared by list/detail queries)

# Display name from auth.users metadata (empty string becomes NULL).
_OWNER_NAME_EXPR = (
    "NULLIF(TRIM(CONCAT_WS(' ', "
    "au_owner.raw_user_meta_data->>'first_name', "
    "au_owner.raw_user_meta_data->>'last_name'"
    ")), '')"
)

_LEADS_ENRICHMENT_JOINS = """
    INNER JOIN clients c
        ON c.id = l.client_id
       AND c.organization_id = l.organization_id
    LEFT JOIN auth.users au_owner
        ON au_owner.id = l.owner_id
    LEFT JOIN clients poc
        ON poc.id = l.point_of_contact
       AND poc.organization_id = l.organization_id
    LEFT JOIN lead_stages ls
        ON ls.id = l.stage_id
       AND ls.organization_id = l.organization_id
"""

# $1 org | $2 stage (optional) | $3 ILIKE pattern for lead or client name (optional)
_LEADS_FILTER_WHERE = """
    WHERE l.organization_id = $1
      AND ($2::uuid IS NULL OR l.stage_id = $2)
      AND ($3::text IS NULL OR l.name ILIKE $3 OR c.name ILIKE $3)
"""

_LEADS_LIST_ORDER_BY = "ORDER BY l.updated_at DESC NULLS LAST, l.created_at DESC"

_SQL_LEADS_LIST = f"""
    SELECT
        l.id,
        l.client_id,
        c.name AS client_name,
        l.name,
        l.stage_id,
        ls.stage_name,
        l.lead_score,
        l.close_date,
        l.amount,
        l.owner_id,
        {_OWNER_NAME_EXPR} AS owner_name,
        l.point_of_contact AS point_of_contact_id,
        poc.name AS point_of_contact,
        l.created_at,
        l.updated_at
    FROM leads l
    {_LEADS_ENRICHMENT_JOINS}
    {_LEADS_FILTER_WHERE}
"""

# List body + sort (kanban uses this; paginated list adds LIMIT/OFFSET).
_SQL_LEADS_LIST_ORDERED = f"{_SQL_LEADS_LIST.strip()}\n    {_LEADS_LIST_ORDER_BY}"

_SQL_LEADS_COUNT_FILTERED = f"""
    SELECT COUNT(*)::int AS n
    FROM leads l
    INNER JOIN clients c
        ON c.id = l.client_id
       AND c.organization_id = l.organization_id
    {_LEADS_FILTER_WHERE}
"""

_SQL_LEAD_DETAIL_BY_ID = f"""
    SELECT
        l.id,
        l.organization_id,
        l.client_id,
        l.name,
        l.stage_id,
        l.lead_status,
        l.intake_stage,
        l.lead_source,
        l.referral_source,
        l.lead_score,
        l.close_date,
        l.converted_at,
        l.notes,
        l.amount,
        l.created_by,
        l.description,
        l.owner_id,
        {_OWNER_NAME_EXPR} AS owner_name,
        l.custom_fields,
        l.created_at,
        l.updated_at,
        l.point_of_contact AS point_of_contact_id,
        poc.name AS point_of_contact,
        c.name AS client_name,
        ls.stage_name
    FROM leads l
    {_LEADS_ENRICHMENT_JOINS}
    WHERE l.organization_id = $1
      AND l.id = $2::uuid
    LIMIT 1
"""

CREATE_LEAD_COLUMNS: tuple[str, ...] = (
    "client_id",
    "organization_id",
    "name",
    "stage_id",
    "lead_status",
    "intake_stage",
    "lead_source",
    "referral_source",
    "lead_score",
    "close_date",
    "converted_at",
    "notes",
    "amount",
    "created_by",
    "description",
    "owner_id",
    "point_of_contact",
    "custom_fields",
)

# PATCH /leads — must stay aligned with service-layer payloads.
LEAD_UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "stage_id",
        "lead_status",
        "intake_stage",
        "lead_source",
        "referral_source",
        "lead_score",
        "close_date",
        "converted_at",
        "notes",
        "amount",
        "description",
        "owner_id",
        "point_of_contact",
        "custom_fields",
    }
)


def _ilike_pattern(search: str | None) -> str | None:
    """Wrap search for optional ILIKE; None means no filter ($3 IS NULL)."""
    return f"%{search}%" if search else None


class LeadRepository:
    """CRUD helpers for the ``leads`` table."""

    TABLE_NAME = "leads"
    JSONB_COLUMNS = frozenset({"custom_fields"})
    UPDATABLE_FIELDS = LEAD_UPDATABLE_FIELDS

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    async def get_client_and_lead_existence(
        self,
        organization_id: str,
        client_id: str,
    ) -> tuple[bool, bool]:
        """Return ``(client_exists, lead_exists)`` for create-lead validation in one query."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
                EXISTS(
                    SELECT 1 FROM clients c
                    WHERE c.organization_id = $1 AND c.id = $2::uuid
                ) AS client_exists,
                EXISTS(
                    SELECT 1 FROM leads l
                    WHERE l.organization_id = $1 AND l.client_id = $2::uuid
                ) AS lead_exists
            """,
            organization_id,
            client_id,
        )
        return bool(row["client_exists"]), bool(row["lead_exists"])

    async def create_lead(self, row: dict[str, Any]) -> dict[str, Any]:
        """Insert a lead row; unknown keys should not be passed (service-layer allowlist)."""
        values: list[Any] = []
        placeholders: list[str] = []
        for i, col in enumerate(CREATE_LEAD_COLUMNS, start=1):
            placeholders.append(f"${i}")
            raw = row.get(col)
            values.append(serialize_jsonb_param(col, raw, self.JSONB_COLUMNS))

        cols_sql = ", ".join(CREATE_LEAD_COLUMNS)
        ph_sql = ", ".join(placeholders)
        query = f"""
            INSERT INTO {self.TABLE_NAME} ({cols_sql})
            VALUES ({ph_sql})
            RETURNING *
        """
        created = await self.db_connection.fetchrow(query, *values)
        return dict(created)

    async def count_leads_filtered(
        self,
        organization_id: str,
        *,
        stage_id: str | None = None,
        search: str | None = None,
    ) -> int:
        """Count leads for org with optional stage and lead/client name search."""
        row = await self.db_connection.fetchrow(
            _SQL_LEADS_COUNT_FILTERED,
            organization_id,
            stage_id,
            _ilike_pattern(search),
        )
        return int(row["n"]) if row else 0

    async def list_leads_page(
        self,
        organization_id: str,
        *,
        stage_id: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Paginated leads with client and stage display names (list mode)."""
        query = f"{_SQL_LEADS_LIST_ORDERED}\n    LIMIT $4::int OFFSET $5::int"
        rows = await self.db_connection.fetch(
            query,
            organization_id,
            stage_id,
            _ilike_pattern(search),
            limit,
            offset,
        )
        return [dict(r) for r in rows]

    async def list_leads_for_kanban(
        self,
        organization_id: str,
        *,
        stage_id: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """All matching leads with joins (kanban mode; grouped in service layer)."""
        rows = await self.db_connection.fetch(
            _SQL_LEADS_LIST_ORDERED,
            organization_id,
            stage_id,
            _ilike_pattern(search),
        )
        return [dict(r) for r in rows]

    async def get_lead_detail_by_id(
        self,
        organization_id: str,
        lead_id: str,
    ) -> dict[str, Any] | None:
        """Single lead with client and stage names; scoped to organization."""
        row = await self.db_connection.fetchrow(
            _SQL_LEAD_DETAIL_BY_ID,
            organization_id,
            lead_id,
        )
        return dict(row) if row else None

    async def delete_leads_by_client_id(self, client_id: str) -> bool:
        """Delete all lead rows linked to ``client_id``."""
        query = """
            DELETE FROM leads
            WHERE client_id = $1
        """
        await self.db_connection.execute(query, client_id)
        return True

    async def update_lead(
        self,
        organization_id: str,
        lead_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch allowed columns; return the updated row or ``None`` if no matching lead."""
        filtered: dict[str, Any] = {
            k: v for k, v in update_data.items() if k in self.UPDATABLE_FIELDS
        }
        if not filtered:
            return await self.get_lead_detail_by_id(organization_id, lead_id)

        set_clauses: list[str] = []
        values: list[Any] = [organization_id, lead_id]
        param_index = 3
        for field, value in filtered.items():
            serialized = serialize_jsonb_param(field, value, self.JSONB_COLUMNS)
            set_clauses.append(f"{field} = ${param_index}")
            values.append(serialized)
            param_index += 1

        query = f"""
            UPDATE {self.TABLE_NAME}
            SET {", ".join(set_clauses)}, updated_at = NOW()
            WHERE organization_id = $1
              AND id = $2::uuid
            RETURNING *
        """
        row = await self.db_connection.fetchrow(query, *values)
        return dict(row) if row else None
