"""Lead persistence (public.leads, public.lead_contacts) — asyncpg."""

from typing import Any

import asyncpg

from apps.user_service.app.utils.common_utils import (
    parse_json_field,
    serialize_jsonb_param,
)

# Optional search: lead name, linked company name, or any linked contact person name (EXISTS only).
_LEADS_SEARCH_PREDICATE = """
      AND (
          $3::text IS NULL
          OR l.name ILIKE $3
          OR EXISTS (
              SELECT 1
              FROM clients cc
              WHERE cc.id = l.client_company_id
                AND cc.organization_id = l.organization_id
                AND cc.name ILIKE $3
          )
          OR EXISTS (
              SELECT 1
              FROM lead_contacts lc
              INNER JOIN clients poc
                  ON poc.id = lc.contact_client_id
                 AND poc.organization_id = lc.organization_id
              WHERE lc.lead_id = l.id
                AND lc.organization_id = l.organization_id
                AND poc.name ILIKE $3
          )
      )
"""

# $1 org | $2 stage (optional) | $3 ILIKE pattern (optional)
_LEADS_FILTER_WHERE = f"""
    WHERE l.organization_id = $1
      AND ($2::uuid IS NULL OR l.stage_id = $2)
    {_LEADS_SEARCH_PREDICATE}
"""

_LEADS_LIST_ORDER_BY = "ORDER BY l.updated_at DESC NULLS LAST, l.created_at DESC"

# Join company, pipeline stage, and owner display name (list/kanban) in one round trip.
_LEADS_JOIN_DISPLAY = """
LEFT JOIN clients comp
    ON comp.id = l.client_company_id
   AND comp.organization_id = l.organization_id
LEFT JOIN lead_stages ls
    ON ls.id = l.stage_id
   AND ls.organization_id = l.organization_id
LEFT JOIN auth.users u ON u.id = l.owner_id
"""

# Mirrors ``UserRepository._display_name_from_meta`` (first_name / last_name in JSON).
_LEAD_OWNER_DISPLAY_NAME_SQL = """
CASE
    WHEN l.owner_id IS NULL THEN NULL::text
    ELSE NULLIF(
        TRIM(
            CONCAT_WS(
                ' ',
                NULLIF(TRIM(COALESCE(u.raw_user_meta_data->>'first_name', '')), ''),
                NULLIF(TRIM(COALESCE(u.raw_user_meta_data->>'last_name', '')), '')
            )
        ),
        ''
    )
END
"""

# Filtered list rows (shared by paginated list, kanban, and window-count query).
_SQL_LEADS_LIST = f"""
    SELECT
        l.id,
        l.client_company_id,
        l.name,
        l.stage_id,
        l.deal_type,
        l.priority,
        l.lead_score,
        l.close_date,
        l.amount,
        l.owner_id,
        l.created_at,
        l.updated_at,
        COALESCE(comp.name, '') AS company_name,
        ls.stage_name AS stage_name,
        ({_LEAD_OWNER_DISPLAY_NAME_SQL.strip()}) AS owner_name
    FROM leads l
    {_LEADS_JOIN_DISPLAY.strip()}
    {_LEADS_FILTER_WHERE}
"""

_SQL_LEADS_LIST_WITH_TOTAL = f"""
    SELECT
        l.id,
        l.client_company_id,
        l.name,
        l.stage_id,
        l.deal_type,
        l.priority,
        l.lead_score,
        l.close_date,
        l.amount,
        l.owner_id,
        l.created_at,
        l.updated_at,
        COALESCE(comp.name, '') AS company_name,
        ls.stage_name           AS stage_name,
        ({_LEAD_OWNER_DISPLAY_NAME_SQL.strip()}) AS owner_name,
        COUNT(*) OVER()         AS total_count
    FROM leads l
    {_LEADS_JOIN_DISPLAY.strip()}
    {_LEADS_FILTER_WHERE}
    {_LEADS_LIST_ORDER_BY}
    LIMIT $4::int OFFSET $5::int
"""

# List body + sort (kanban + paginated list).
_SQL_LEADS_LIST_ORDERED = f"{_SQL_LEADS_LIST.strip()}\n    {_LEADS_LIST_ORDER_BY}"

_SQL_LEAD_DETAIL_BY_ID = f"""
    SELECT
        l.id,
        l.organization_id,
        l.client_company_id,
        l.name,
        l.stage_id,
        l.lead_source,
        l.referral_source,
        l.lead_score,
        l.deal_type,
        l.priority,
        l.close_date,
        l.notes,
        l.amount,
        l.description,
        l.owner_id,
        l.custom_fields,
        l.created_at,
        l.updated_at,
        COALESCE(comp.name, '') AS company_name,
        ls.stage_name AS stage_name,
        ({_LEAD_OWNER_DISPLAY_NAME_SQL.strip()}) AS owner_name
    FROM leads l
    {_LEADS_JOIN_DISPLAY.strip()}
    WHERE l.organization_id = $1
      AND l.id = $2::uuid
    LIMIT 1
"""

_SQL_LEAD_DETAIL_WITH_CONTACTS_FLAT_BY_ID = f"""
    SELECT
        l.id,
        l.organization_id,
        l.client_company_id,
        l.name,
        l.stage_id,
        l.lead_source,
        l.referral_source,
        l.lead_score,
        l.deal_type,
        l.priority,
        l.close_date,
        l.notes,
        l.amount,
        l.description,
        l.owner_id,
        l.custom_fields,
        l.created_at,
        l.updated_at,
        COALESCE(comp.name, '') AS company_name,
        ls.stage_name AS stage_name,
        ({_LEAD_OWNER_DISPLAY_NAME_SQL.strip()}) AS owner_name,
        lc.contact_client_id AS contact_client_id,
        lc.label AS label,
        c.name AS contact_name
    FROM leads l
    {_LEADS_JOIN_DISPLAY.strip()}
    LEFT JOIN lead_contacts lc
        ON lc.lead_id = l.id
       AND lc.organization_id = l.organization_id
    LEFT JOIN clients c
        ON c.id = lc.contact_client_id
       AND c.organization_id = lc.organization_id
    WHERE l.organization_id = $1
      AND l.id = $2::uuid
    ORDER BY lc.created_at ASC
"""

CREATE_LEAD_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "name",
    "stage_id",
    "client_company_id",
    "lead_source",
    "referral_source",
    "lead_score",
    "deal_type",
    "priority",
    "close_date",
    "amount",
    "description",
    "notes",
    "custom_fields",
    "owner_id",
)

# PATCH /leads — must stay aligned with service-layer payloads.
LEAD_UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "stage_id",
        "lead_source",
        "referral_source",
        "lead_score",
        "close_date",
        "amount",
        "description",
        "owner_id",
        "client_company_id",
        "deal_type",
        "priority",
        "notes",
        "custom_fields",
    }
)


def _ilike_pattern(search: str | None) -> str | None:
    """Wrap search for optional ILIKE; None means no filter ($3 IS NULL)."""
    return f"%{search}%" if search else None


class LeadRepository:
    """CRUD helpers for the ``leads`` and ``lead_contacts`` tables."""

    TABLE_NAME = "leads"
    JSONB_COLUMNS = frozenset({"custom_fields", "notes"})
    UPDATABLE_FIELDS = LEAD_UPDATABLE_FIELDS

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    async def fetch_company_names(
        self,
        organization_id: str,
        company_ids: list[str],
    ) -> dict[str, str]:
        """Map client id -> display name (used when a row lacks joined ``company_name``)."""
        if not company_ids:
            return {}
        rows = await self.db_connection.fetch(
            """
            SELECT id, name
            FROM clients
            WHERE organization_id = $1
              AND id = ANY($2::uuid[])
            """,
            organization_id,
            company_ids,
        )
        return {str(r["id"]): (r["name"] or "") for r in rows}

    async def fetch_lead_reference_validation(
        self,
        organization_id: str,
        client_ids: list[str],
        *,
        stage_id: str | None = None,
    ) -> tuple[bool | None, dict[str, str]]:
        """Single round trip: optional pipeline stage check + ``client_id -> client_type`` map.

        When ``stage_id`` is ``None``, the stage is not checked and the first tuple value is
        ``None``. When ``client_ids`` is empty and ``stage_id`` is also ``None``, returns
        ``(None, {})`` without hitting the database.

        Omitted clients (wrong org or unknown id) are absent from the map, same as before.
        """
        if not client_ids and stage_id is None:
            return None, {}

        row = await self.db_connection.fetchrow(
            """
            SELECT
                CASE
                    WHEN $2::uuid IS NULL THEN NULL::boolean
                    ELSE EXISTS(
                        SELECT 1 FROM lead_stages ls
                        WHERE ls.organization_id = $1::uuid AND ls.id = $2::uuid
                    )
                END AS stage_exists,
                COALESCE(
                    (
                        SELECT jsonb_object_agg(c.id::text, to_jsonb(c.client_type::text))
                        FROM clients c
                        WHERE c.organization_id = $1::uuid
                          AND c.id = ANY($3::uuid[])
                    ),
                    '{}'::jsonb
                ) AS client_types
            """,
            organization_id,
            stage_id,
            client_ids if client_ids else [],
        )
        if row is None:
            return None, {}
        raw_stage = row["stage_exists"]
        stage_ok: bool | None = None if raw_stage is None else bool(raw_stage)
        raw_types = row["client_types"]
        parsed = parse_json_field(raw_types)
        if isinstance(parsed, dict):
            types_map = {str(k): str(v) for k, v in parsed.items()}
        else:
            types_map = {}
        return stage_ok, types_map

    async def create_lead(
        self,
        row: dict[str, Any],
        contacts: list[tuple[str, str | None]] | None = None,
    ) -> dict[str, Any]:
        """Insert a lead row; with contacts, insert ``lead_contacts`` in the same statement.

        Unknown keys in ``row`` should not be passed (service-layer allowlist).
        ``contacts`` is ``(contact_client_id, label)`` pairs; omit or pass
        ``[]`` when there are no person links (uses a plain ``INSERT`` so empty
        arrays are never bound as ``uuid[]``/``text[]``, which asyncpg/Postgres
        often reject).
        """
        pairs = contacts or []

        values: list[Any] = []
        placeholders: list[str] = []
        for i, col in enumerate(CREATE_LEAD_COLUMNS, start=1):
            placeholders.append(f"${i}")
            raw = row.get(col)
            values.append(serialize_jsonb_param(col, raw, self.JSONB_COLUMNS))

        cols_sql = ", ".join(CREATE_LEAD_COLUMNS)
        ph_sql = ", ".join(placeholders)

        if not pairs:
            # Avoid ``unnest($n::uuid[], ...)`` with empty Python lists: drivers often
            # cannot infer ``uuid[]`` / ``text[]`` for ``{}``, which breaks binding.
            query = f"""
                INSERT INTO {self.TABLE_NAME} ({cols_sql})
                VALUES ({ph_sql})
                RETURNING *
            """
            created = await self.db_connection.fetchrow(query, *values)
            return dict(created)

        contact_ids = [cid for cid, _ in pairs]
        labels = [lab for _, lab in pairs]
        num_columns = len(CREATE_LEAD_COLUMNS)
        p_ids = num_columns + 1
        p_labels = num_columns + 2
        query = f"""
            WITH new_lead AS (
                INSERT INTO {self.TABLE_NAME} ({cols_sql})
                VALUES ({ph_sql})
                RETURNING *
            ),
            _ins_contacts AS (
                INSERT INTO lead_contacts (lead_id, organization_id, contact_client_id, label)
                SELECT nl.id, nl.organization_id, u.contact_client_id, u.label
                FROM new_lead nl
                CROSS JOIN LATERAL unnest(${p_ids}::uuid[], ${p_labels}::text[])
                    AS u(contact_client_id, label)
                RETURNING 1
            )
            SELECT nl.*
            FROM new_lead nl
            WHERE (SELECT count(*)::int FROM _ins_contacts) >= 0
        """
        created = await self.db_connection.fetchrow(query, *values, contact_ids, labels)
        return dict(created)

    async def list_lead_contacts_for_lead(
        self,
        organization_id: str,
        lead_id: str,
    ) -> list[dict[str, Any]]:
        """Person contacts for a lead with resolved display names."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                lc.contact_client_id,
                lc.label,
                c.name AS contact_name
            FROM lead_contacts lc
            INNER JOIN clients c
                ON c.id = lc.contact_client_id
               AND c.organization_id = lc.organization_id
            WHERE lc.lead_id = $1::uuid
              AND lc.organization_id = $2::uuid
            ORDER BY lc.created_at ASC
            """,
            lead_id,
            organization_id,
        )
        return [dict(r) for r in rows]

    async def list_leads_page_with_total(
        self,
        organization_id: str,
        *,
        stage_id: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated leads (list mode) with company, stage, and owner display columns."""
        rows = await self.db_connection.fetch(
            _SQL_LEADS_LIST_WITH_TOTAL,
            organization_id,
            stage_id,
            _ilike_pattern(search),
            limit,
            offset,
        )
        if not rows:
            return [], 0
        return [dict(r) for r in rows], int(rows[0]["total_count"])

    async def list_leads_for_kanban(
        self,
        organization_id: str,
        *,
        stage_id: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """All matching leads (kanban) with company, stage, and owner display columns."""
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
        """Single lead scoped to organization with company, stage, and owner display columns."""
        row = await self.db_connection.fetchrow(
            _SQL_LEAD_DETAIL_BY_ID,
            organization_id,
            lead_id,
        )
        return dict(row) if row else None

    async def get_lead_detail_with_contacts_by_id(
        self,
        organization_id: str,
        lead_id: str,
    ) -> dict[str, Any] | None:
        """Lead row (company, stage, owner display) plus contacts.

        Uses a single indexed query (lead scoped by org + id). It returns one row
        per contact, then aggregates contacts in Python.
        """
        rows = await self.db_connection.fetch(
            _SQL_LEAD_DETAIL_WITH_CONTACTS_FLAT_BY_ID,
            organization_id,
            lead_id,
        )
        if not rows:
            return None

        payload = dict(rows[0])
        # Remove flattened contact columns so the response stays consistent with
        # the service-layer expectation: `row["contacts"]` only.
        payload.pop("contact_client_id", None)
        payload.pop("label", None)
        payload.pop("contact_name", None)

        contacts: list[dict[str, Any]] = []
        for row in rows:
            contact_client_id = row["contact_client_id"]
            if contact_client_id is None:
                continue
            contacts.append(
                {
                    "contact_client_id": contact_client_id,
                    "label": row["label"],
                    "contact_name": row["contact_name"],
                }
            )

        payload["contacts"] = contacts
        return payload

    async def delete_lead(
        self,
        organization_id: str,
        lead_id: str,
    ) -> dict[str, Any] | None:
        """Hard-delete one lead scoped to the organization; return removed row if any."""
        row = await self.db_connection.fetchrow(
            f"""
            DELETE FROM {self.TABLE_NAME}
            WHERE organization_id = $1
              AND id = $2::uuid
            RETURNING *
            """,
            organization_id,
            lead_id,
        )
        return dict(row) if row else None

    async def delete_leads_by_client_id(self, client_id: str) -> bool:
        """Remove lead links for a deleted client: contacts and company association."""
        await self.db_connection.execute(
            """
            DELETE FROM lead_contacts
            WHERE contact_client_id = $1::uuid
            """,
            client_id,
        )
        await self.db_connection.execute(
            """
            DELETE FROM leads
            WHERE client_company_id = $1::uuid
            """,
            client_id,
        )
        return True

    async def _sync_contacts(
        self,
        organization_id: str,
        lead_id: str,
        contact_ids: list[str],
        labels: list[str | None],
    ) -> None:
        """Reconcile ``lead_contacts`` to the desired contact ids and labels."""
        if not contact_ids:
            await self.db_connection.execute(
                """
                DELETE FROM lead_contacts
                WHERE lead_id = $1::uuid
                  AND organization_id = $2::uuid
                """,
                lead_id,
                organization_id,
            )
            return

        await self.db_connection.execute(
            """
            WITH desired AS (
                SELECT
                    u.contact_client_id::uuid,
                    u.label::text
                FROM unnest($3::uuid[], $4::text[])
                    AS u(contact_client_id, label)
            ),

            deleted AS (
                DELETE FROM lead_contacts lc
                WHERE lc.lead_id = $1::uuid
                  AND lc.organization_id = $2::uuid
                  AND NOT EXISTS (
                      SELECT 1 FROM desired d
                      WHERE d.contact_client_id = lc.contact_client_id
                  )
                RETURNING 1
            ),

            updated AS (
                UPDATE lead_contacts lc
                SET
                    label = d.label,
                    updated_at = NOW()
                FROM desired d
                WHERE lc.lead_id = $1::uuid
                  AND lc.organization_id = $2::uuid
                  AND lc.contact_client_id = d.contact_client_id
                  AND lc.label IS DISTINCT FROM d.label
                RETURNING 1
            )

            INSERT INTO lead_contacts (
                lead_id,
                organization_id,
                contact_client_id,
                label
            )
            SELECT
                $1::uuid,
                $2::uuid,
                d.contact_client_id,
                d.label
            FROM desired d
            WHERE NOT EXISTS (
                SELECT 1 FROM lead_contacts lc
                WHERE lc.lead_id = $1::uuid
                  AND lc.organization_id = $2::uuid
                  AND lc.contact_client_id = d.contact_client_id
            )
              AND (SELECT count(*)::bigint FROM deleted) >= 0
              AND (SELECT count(*)::bigint FROM updated) >= 0
            """,
            lead_id,
            organization_id,
            contact_ids,
            labels,
        )

    async def update_lead_with_contacts(
        self,
        organization_id: str,
        lead_id: str,
        update_data: dict[str, Any],
        contacts_payload: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        """Apply scalar updates and/or sync contacts; return detail row or ``None`` if missing."""
        filtered: dict[str, Any] = {
            k: v for k, v in update_data.items() if k in self.UPDATABLE_FIELDS
        }

        if filtered:
            set_clauses: list[str] = []
            values: list[Any] = [organization_id, lead_id]
            param_index = 3

            for field, value in filtered.items():
                serialized = serialize_jsonb_param(field, value, self.JSONB_COLUMNS)
                set_clauses.append(f"{field} = ${param_index}")
                values.append(serialized)
                param_index += 1

            row = await self.db_connection.fetchrow(
                f"""
                UPDATE leads
                SET {", ".join(set_clauses)}, updated_at = NOW()
                WHERE organization_id = $1
                  AND id = $2::uuid
                RETURNING id
                """,
                *values,
            )

        elif contacts_payload is not None:
            row = await self.db_connection.fetchrow(
                """
                SELECT id FROM leads
                WHERE organization_id = $1
                  AND id = $2::uuid
                FOR UPDATE
                """,
                organization_id,
                lead_id,
            )
        else:
            return await self.get_lead_detail_by_id(organization_id, lead_id)

        if not row:
            return None

        if contacts_payload is not None:
            c_ids = [str(c["contact_client_id"]) for c in contacts_payload]
            lbls = [c.get("label") for c in contacts_payload]

            await self._sync_contacts(
                organization_id,
                lead_id,
                c_ids,
                lbls,
            )

        return await self.get_lead_detail_by_id(organization_id, lead_id)

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

        # Single round trip: update + return the same enriched columns
        # as `get_lead_detail_by_id()` (company, stage, owner display names).
        query = f"""
            WITH l AS (
                UPDATE {self.TABLE_NAME}
                SET {", ".join(set_clauses)}, updated_at = NOW()
                WHERE organization_id = $1
                  AND id = $2::uuid
                RETURNING *
            )
            SELECT
                l.id,
                l.organization_id,
                l.client_company_id,
                l.name,
                l.stage_id,
                l.lead_source,
                l.referral_source,
                l.lead_score,
                l.deal_type,
                l.priority,
                l.close_date,
                l.notes,
                l.amount,
                l.description,
                l.owner_id,
                l.custom_fields,
                l.created_at,
                l.updated_at,
                COALESCE(comp.name, '') AS company_name,
                ls.stage_name AS stage_name,
                ({_LEAD_OWNER_DISPLAY_NAME_SQL.strip()}) AS owner_name
            FROM l
            {_LEADS_JOIN_DISPLAY.strip()}
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, *values)
        if not row:
            return None
        return dict(row)
