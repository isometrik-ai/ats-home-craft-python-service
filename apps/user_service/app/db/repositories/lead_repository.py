"""Lead persistence (public.leads, public.lead_contacts, public.lead_companies) — asyncpg."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import ClientStatus
from apps.user_service.app.utils.common_utils import (
    json_dumps_or_none,
    parse_json_field,
    serialize_jsonb_param,
)
from libs.shared_utils.custom_field_filtering import build_dropdown_jsonb_where

_CONTACT_DISPLAY_NAME_SQL = """
NULLIF(
    TRIM(
        CONCAT_WS(
            ' ',
            NULLIF(TRIM(COALESCE(ct.first_name, '')), ''),
            NULLIF(TRIM(COALESCE(ct.middle_name, '')), ''),
            NULLIF(TRIM(COALESCE(ct.last_name, '')), '')
        )
    ),
    ''
)
"""

# Owner display from organization_members (scoped to lead org; no auth.users fallback).
_LEAD_OWNER_DISPLAY_NAME_SQL = """
CASE
    WHEN l.owner_id IS NULL THEN NULL::text
    ELSE NULLIF(
        TRIM(
            CONCAT_WS(
                ' ',
                NULLIF(TRIM(COALESCE(om.first_name, '')), ''),
                NULLIF(TRIM(COALESCE(om.last_name, '')), '')
            )
        ),
        ''
    )
END
"""

# Optional search: lead name, company name, contact name/email/phone, or owner display name.
_LEADS_SEARCH_PREDICATE = f"""
      AND (
          $6::text IS NULL
          OR l.name ILIKE $6
          OR ({_LEAD_OWNER_DISPLAY_NAME_SQL.strip()}) ILIKE $6
          OR EXISTS (
              SELECT 1
              FROM lead_companies lco
              INNER JOIN companies co
                  ON co.id = lco.company_id
                 AND co.organization_id = lco.organization_id
                 AND co.status != '{ClientStatus.DELETED.value}'
              WHERE lco.lead_id = l.id
                AND lco.organization_id = l.organization_id
                AND co.name ILIKE $6
          )
          OR EXISTS (
              SELECT 1
              FROM lead_contacts lct
              INNER JOIN contacts ct
                  ON ct.id = lct.contact_id
                 AND ct.organization_id = lct.organization_id
                 AND ct.status != '{ClientStatus.DELETED.value}'
              LEFT JOIN auth.users cu
                  ON cu.id = ct.user_id
              WHERE lct.lead_id = l.id
                AND lct.organization_id = l.organization_id
                AND (
                    ({_CONTACT_DISPLAY_NAME_SQL.strip()}) ILIKE $6
                    OR cu.email::text ILIKE $6
                )
          )
      )
"""

_LEADS_FILTER_WHERE = f"""
    WHERE l.organization_id = $1
      AND ($2::uuid IS NULL OR l.stage_id = $2)
      AND ($3::uuid IS NULL OR l.owner_id = $3)
      AND ($4::date IS NULL OR l.created_at::date >= $4::date)
      AND ($5::date IS NULL OR l.created_at::date <= $5::date)
    {_LEADS_SEARCH_PREDICATE}
"""

_LEADS_LIST_ORDER_BY = "ORDER BY l.updated_at DESC NULLS LAST, l.created_at DESC"

_LEAD_COMPANIES_AGG_SQL = f"""
COALESCE(
    (
        SELECT json_agg(
            json_build_object(
                'company_id', lc.company_id::text,
                'label', lc.label,
                'company_name', COALESCE(co.name, ''),
                'profile_photo_url', co.profile_photo_url
            )
            ORDER BY lc.created_at ASC
        )
        FROM lead_companies lc
        INNER JOIN companies co
            ON co.id = lc.company_id
           AND co.organization_id = lc.organization_id
           AND co.status != '{ClientStatus.DELETED.value}'
        WHERE lc.lead_id = l.id
          AND lc.organization_id = l.organization_id
    ),
    '[]'::json
) AS companies
"""

_CONTACT_ADDRESSES_JSON_FOR_LC_SQL = """
COALESCE(
    (
        SELECT json_agg(
            to_jsonb(addr)
            ORDER BY addr.is_primary DESC, addr.created_at ASC
        )
        FROM contact_addresses addr
        WHERE addr.contact_id = lc.contact_id
    ),
    '[]'::json
)
"""

_CONTACT_ADDRESSES_JSON_FOR_D_SQL = """
COALESCE(
    (
        SELECT json_agg(
            to_jsonb(addr)
            ORDER BY addr.is_primary DESC, addr.created_at ASC
        )
        FROM contact_addresses addr
        WHERE addr.contact_id = d.contact_id
    ),
    '[]'::json
)
"""

_LEAD_CONTACTS_AGG_SQL = f"""
COALESCE(
    (
        SELECT json_agg(
            json_build_object(
                'contact_id', lc.contact_id::text,
                'label', lc.label,
                'contact_name', ({_CONTACT_DISPLAY_NAME_SQL.strip()}),
                'profile_photo_url', ct.profile_photo_url,
                'addresses', ({_CONTACT_ADDRESSES_JSON_FOR_LC_SQL.strip()})
            )
            ORDER BY lc.created_at ASC
        )
        FROM lead_contacts lc
        INNER JOIN contacts ct
            ON ct.id = lc.contact_id
           AND ct.organization_id = lc.organization_id
           AND ct.status != '{ClientStatus.DELETED.value}'
        WHERE lc.lead_id = l.id
          AND lc.organization_id = l.organization_id
    ),
    '[]'::json
) AS contacts
"""

# Join pipeline stage and owner display (list/kanban/detail) in one round trip.
_LEADS_JOIN_DISPLAY = """
LEFT JOIN lead_stages ls
    ON ls.id = l.stage_id
   AND ls.organization_id = l.organization_id
LEFT JOIN organization_members om
    ON om.user_id = l.owner_id
   AND om.organization_id = l.organization_id
   AND om.status != 'deleted'
"""

# Filtered list rows (shared by paginated list, kanban, and window-count query).
_SQL_LEADS_LIST = f"""
    SELECT
        l.id,
        l.name,
        l.stage_id,
        l.deal_type,
        l.priority,
        l.lead_score,
        l.close_date,
        l.amount,
        l.currency,
        l.owner_id,
        l.created_at,
        l.updated_at,
        {_LEAD_COMPANIES_AGG_SQL.strip()},
        {_LEAD_CONTACTS_AGG_SQL.strip()},
        ls.stage_name AS stage_name,
        ({_LEAD_OWNER_DISPLAY_NAME_SQL.strip()}) AS owner_name
    FROM leads l
    {_LEADS_JOIN_DISPLAY.strip()}
    {_LEADS_FILTER_WHERE}
"""

_SQL_LEAD_DETAIL_BY_ID = f"""
    SELECT
        l.id,
        l.organization_id,
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
        l.currency,
        l.description,
        l.owner_id,
        l.custom_fields,
        l.created_at,
        l.updated_at,
        {_LEAD_COMPANIES_AGG_SQL.strip()},
        {_LEAD_CONTACTS_AGG_SQL.strip()},
        ls.stage_name AS stage_name,
        ({_LEAD_OWNER_DISPLAY_NAME_SQL.strip()}) AS owner_name,
        om.email::text AS owner_email
    FROM leads l
    {_LEADS_JOIN_DISPLAY.strip()}
    WHERE l.organization_id = $1
      AND l.id = $2::uuid
      AND ($3::uuid IS NULL OR l.owner_id = $3)
    LIMIT 1
"""

_SQL_LEAD_DETAIL_WITH_CONTACTS_FLAT_BY_ID = f"""
    SELECT
        l.id,
        l.organization_id,
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
        l.currency,
        l.description,
        l.owner_id,
        l.custom_fields,
        l.created_at,
        l.updated_at,
        {_LEAD_COMPANIES_AGG_SQL.strip()},
        ls.stage_name AS stage_name,
        ({_LEAD_OWNER_DISPLAY_NAME_SQL.strip()}) AS owner_name,
        om.email::text AS owner_email,
        lc.contact_id AS contact_id,
        ct.id AS contact_record_id,
        lc.label AS label,
        ({_CONTACT_DISPLAY_NAME_SQL.strip()}) AS contact_name,
        cu.email::text AS contact_email,
        ct.phones AS contact_phones,
        ct.profile_photo_url AS contact_profile_photo_url,
        COALESCE(contact_addr_rows.addresses, '[]'::jsonb) AS contact_addresses
    FROM leads l
    {_LEADS_JOIN_DISPLAY.strip()}
    LEFT JOIN lead_contacts lc
        ON lc.lead_id = l.id
       AND lc.organization_id = l.organization_id
    LEFT JOIN contacts ct
        ON ct.id = lc.contact_id
       AND ct.organization_id = lc.organization_id
       AND ct.status != '{ClientStatus.DELETED.value}'
    LEFT JOIN auth.users cu
        ON cu.id = ct.user_id
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(
            to_jsonb(addr) ORDER BY addr.is_primary DESC, addr.created_at ASC
        ) AS addresses
        FROM contact_addresses addr
        WHERE addr.contact_id = ct.id
    ) contact_addr_rows ON TRUE
    WHERE l.organization_id = $1
      AND l.id = $2::uuid
      AND ($3::uuid IS NULL OR l.owner_id = $3)
    ORDER BY lc.created_at ASC
"""

CREATE_LEAD_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "name",
    "stage_id",
    "lead_source",
    "referral_source",
    "lead_score",
    "deal_type",
    "priority",
    "close_date",
    "amount",
    "currency",
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
        "currency",
        "description",
        "owner_id",
        "deal_type",
        "priority",
        "notes",
        "custom_fields",
    }
)


def _ilike_pattern(search: str | None) -> str | None:
    """Wrap search for optional ILIKE; ``None`` means no filter (placeholder IS NULL)."""
    return f"%{search}%" if search else None


def _leads_list_base_filter_args(
    organization_id: str,
    *,
    stage_id: str | None = None,
    owner_id: str | None = None,
    start_date: Any = None,
    end_date: Any = None,
    search: str | None = None,
) -> list[Any]:
    """Build args bound to ``_LEADS_FILTER_WHERE`` placeholders ``$1`` … ``$6`` (in order).

    Any extra predicates must extend this tuple and renumber ``_LEADS_FILTER_WHERE`` together.
    """
    return [
        organization_id,
        stage_id,
        owner_id,
        start_date,
        end_date,
        _ilike_pattern(search),
    ]


class LeadRepository:
    """CRUD helpers for ``leads``, ``lead_contacts``, and ``lead_companies``."""

    TABLE_NAME = "leads"
    JSONB_COLUMNS = frozenset({"custom_fields", "notes"})
    UPDATABLE_FIELDS = LEAD_UPDATABLE_FIELDS

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    async def fetch_lead_reference_validation(
        self,
        organization_id: str,
        *,
        stage_id: str | None = None,
        contact_ids: list[str] | None = None,
        company_ids: list[str] | None = None,
    ) -> tuple[bool | None, set[str], set[str]]:
        """Pipeline stage check plus which contact/company ids exist in the org (not deleted).

        When there is nothing to validate (no stage and no ids), skips the DB and returns
        ``(None, set(), set())``.
        """
        cids = contact_ids or []
        gids = company_ids or []
        if stage_id is None and not cids and not gids:
            return None, set(), set()

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
                        SELECT array_agg(c.id::text)
                        FROM contacts c
                        WHERE c.organization_id = $1::uuid
                          AND c.id = ANY($3::uuid[])
                          AND c.status != $5::text
                    ),
                    ARRAY[]::text[]
                ) AS found_contacts,
                COALESCE(
                    (
                        SELECT array_agg(g.id::text)
                        FROM companies g
                        WHERE g.organization_id = $1::uuid
                          AND g.id = ANY($4::uuid[])
                          AND g.status != $5::text
                    ),
                    ARRAY[]::text[]
                ) AS found_companies
            """,
            organization_id,
            stage_id,
            cids if cids else [],
            gids if gids else [],
            ClientStatus.DELETED.value,
        )
        if row is None:
            return None, set(), set()
        raw_stage = row["stage_exists"]
        stage_ok: bool | None = None if raw_stage is None else bool(raw_stage)
        found_contacts = row["found_contacts"] or []
        found_companies = row["found_companies"] or []
        return stage_ok, {str(x) for x in found_contacts}, {str(x) for x in found_companies}

    async def create_lead(
        self,
        row: dict[str, Any],
        contacts: list[tuple[str, str | None]] | None = None,
        companies: list[tuple[str, str | None]] | None = None,
    ) -> dict[str, Any]:
        """Insert a lead; optional ``lead_companies`` and ``lead_contacts``."""
        pairs = contacts or []
        company_rows = companies or []

        values: list[Any] = []
        placeholders: list[str] = []
        for i, col in enumerate(CREATE_LEAD_COLUMNS, start=1):
            placeholders.append(f"${i}")
            raw = row.get(col)
            values.append(serialize_jsonb_param(col, raw, self.JSONB_COLUMNS))

        cols_sql = ", ".join(CREATE_LEAD_COLUMNS)
        ph_sql = ", ".join(placeholders)

        insert_lead_sql = f"""
            INSERT INTO {self.TABLE_NAME} ({cols_sql})
            VALUES ({ph_sql})
            RETURNING *
        """
        created = await self.db_connection.fetchrow(insert_lead_sql, *values)
        if created is None:
            return {}
        lead_id = created["id"]
        org_id = created["organization_id"]

        if company_rows:
            cids = [c for c, _ in company_rows]
            labs = [lab for _, lab in company_rows]
            await self.db_connection.execute(
                """
                INSERT INTO lead_companies (lead_id, organization_id, company_id, label)
                SELECT $1::uuid, $2::uuid, u.company_id, u.label
                FROM unnest($3::uuid[], $4::text[])
                    AS u(company_id, label)
                """,
                lead_id,
                org_id,
                cids,
                labs,
            )

        if not pairs:
            return dict(created)

        contact_ids = [cid for cid, _ in pairs]
        labels = [lab for _, lab in pairs]
        await self.db_connection.execute(
            """
            INSERT INTO lead_contacts (lead_id, organization_id, contact_id, label)
            SELECT $1::uuid, $2::uuid, u.contact_id, u.label
            FROM unnest($3::uuid[], $4::text[])
                AS u(contact_id, label)
            """,
            lead_id,
            org_id,
            contact_ids,
            labels,
        )
        return dict(created)

    async def list_leads_page_with_total(
        self,
        organization_id: str,
        *,
        stage_id: str | None = None,
        owner_id: str | None = None,
        start_date: Any = None,
        end_date: Any = None,
        search: str | None = None,
        dropdown_filters: dict[str, list[str]] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated leads (list mode) with companies, stage, and owner display columns."""
        args = _leads_list_base_filter_args(
            organization_id,
            stage_id=stage_id,
            owner_id=owner_id,
            start_date=start_date,
            end_date=end_date,
            search=search,
        )
        where_extra = ""
        next_param_index = len(args) + 1
        if dropdown_filters:
            dropdown_where, dropdown_args, next_param_index = build_dropdown_jsonb_where(
                custom_fields_column_sql="l.custom_fields",
                filters=dropdown_filters,
                param_start_index=next_param_index,
            )
            if dropdown_where:
                where_extra = f" AND ({dropdown_where})"
                args.extend(dropdown_args)

        limit_idx = next_param_index
        offset_idx = next_param_index + 1
        args.extend([limit, offset])

        sql = f"""
    SELECT
        l.id,
        l.name,
        l.stage_id,
        l.deal_type,
        l.priority,
        l.lead_score,
        l.close_date,
        l.amount,
        l.currency,
        l.owner_id,
        l.created_at,
        l.updated_at,
        {_LEAD_COMPANIES_AGG_SQL.strip()},
        {_LEAD_CONTACTS_AGG_SQL.strip()},
        ls.stage_name           AS stage_name,
        ({_LEAD_OWNER_DISPLAY_NAME_SQL.strip()}) AS owner_name,
        COUNT(*) OVER()         AS total_count
    FROM leads l
    {_LEADS_JOIN_DISPLAY.strip()}
    {_LEADS_FILTER_WHERE}
    {where_extra}
    {_LEADS_LIST_ORDER_BY}
    LIMIT ${limit_idx}::int OFFSET ${offset_idx}::int
"""
        rows = await self.db_connection.fetch(sql, *args)
        if not rows:
            return [], 0
        return [dict(r) for r in rows], int(rows[0]["total_count"])

    async def list_leads_for_kanban(
        self,
        organization_id: str,
        *,
        stage_id: str | None = None,
        owner_id: str | None = None,
        start_date: Any = None,
        end_date: Any = None,
        search: str | None = None,
        dropdown_filters: dict[str, list[str]] | None = None,
    ) -> list[dict[str, Any]]:
        """All matching leads (kanban) with companies, stage, and owner display columns."""
        args = _leads_list_base_filter_args(
            organization_id,
            stage_id=stage_id,
            owner_id=owner_id,
            start_date=start_date,
            end_date=end_date,
            search=search,
        )
        where_extra = ""
        if dropdown_filters:
            dropdown_where, dropdown_args, _ = build_dropdown_jsonb_where(
                custom_fields_column_sql="l.custom_fields",
                filters=dropdown_filters,
                param_start_index=len(args) + 1,
            )
            if dropdown_where:
                where_extra = f" AND ({dropdown_where})"
                args.extend(dropdown_args)

        sql = (
            _SQL_LEADS_LIST.strip()
            + (f"\n    {where_extra}" if where_extra else "")
            + f"\n    {_LEADS_LIST_ORDER_BY}"
        )

        rows = await self.db_connection.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def get_lead_detail_by_id(
        self,
        organization_id: str,
        lead_id: str,
        *,
        owner_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Single lead scoped to organization with companies, stage, and owner display columns."""
        row = await self.db_connection.fetchrow(
            _SQL_LEAD_DETAIL_BY_ID,
            organization_id,
            lead_id,
            owner_id,
        )
        return dict(row) if row else None

    async def get_lead_detail_with_contacts_by_id(
        self,
        organization_id: str,
        lead_id: str,
        *,
        owner_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Lead row (companies, stage, owner display) plus contacts.

        Uses a single indexed query (lead scoped by org + id). It returns one row
        per contact, then aggregates contacts in Python.
        """
        rows = await self.db_connection.fetch(
            _SQL_LEAD_DETAIL_WITH_CONTACTS_FLAT_BY_ID,
            organization_id,
            lead_id,
            owner_id,
        )
        if not rows:
            return None

        payload = dict(rows[0])
        payload.pop("contact_id", None)
        payload.pop("label", None)
        payload.pop("contact_name", None)
        payload.pop("contact_email", None)
        payload.pop("contact_phones", None)
        payload.pop("contact_profile_photo_url", None)
        payload.pop("contact_addresses", None)

        contacts: list[dict[str, Any]] = []
        for row in rows:
            contact_id = row["contact_id"]
            if contact_id is None:
                continue
            if row.get("contact_record_id") is None:
                continue
            contacts.append(
                {
                    "contact_id": contact_id,
                    "label": row["label"],
                    "contact_name": row["contact_name"],
                    "email": row.get("contact_email"),
                    "phones": parse_json_field(row.get("contact_phones")) or [],
                    "profile_photo_url": row.get("contact_profile_photo_url"),
                    "addresses": parse_json_field(row.get("contact_addresses")) or [],
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
        """Deprecated: leads no longer reference ``clients``.

        Kept so older call sites do not break; junction cleanup is tied to contact/company
        lifecycle elsewhere.
        """
        _ = client_id
        return True

    async def update_lead_with_associations(
        self,
        organization_id: str,
        lead_id: str,
        update_data: dict[str, Any],
        contacts_payload: list[dict[str, Any]] | None,
        companies_payload: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Apply scalar updates and/or sync contacts/companies; return detail row or ``None``."""
        filtered: dict[str, Any] = {
            k: v for k, v in update_data.items() if k in self.UPDATABLE_FIELDS
        }

        if not filtered and contacts_payload is None and companies_payload is None:
            return await self.get_lead_detail_by_id(organization_id, lead_id)

        return await self.update_lead_and_sync_associations(
            organization_id=organization_id,
            lead_id=lead_id,
            update_data=filtered,
            contacts_payload=contacts_payload,
            companies_payload=companies_payload,
        )

    async def update_lead_and_sync_associations(
        self,
        *,
        organization_id: str,
        lead_id: str,
        update_data: dict[str, Any],
        contacts_payload: list[dict[str, Any]] | None,
        companies_payload: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        """Single-query lead update + association sync + final snapshot (Postgres CTE).

        This reduces DB round trips by applying:
        - scalar lead updates
        - lead_contacts sync (when contacts_payload is not None)
        - lead_companies sync (when companies_payload is not None)
        and returning the final lead detail row (with `companies` + `contacts`)
        in a single `fetchrow`.
        """
        filtered: dict[str, Any] = {
            k: v for k, v in update_data.items() if k in self.UPDATABLE_FIELDS
        }

        contacts_payload_json = json_dumps_or_none(contacts_payload)
        companies_payload_json = json_dumps_or_none(companies_payload)

        set_clauses: list[str] = []
        values: list[Any] = [organization_id, lead_id]
        param_index = 3
        for field, value in filtered.items():
            serialized = serialize_jsonb_param(field, value, self.JSONB_COLUMNS)
            set_clauses.append(f"{field} = ${param_index}")
            values.append(serialized)
            param_index += 1

        contacts_param = f"${param_index}"
        values.append(contacts_payload_json)
        param_index += 1
        companies_param = f"${param_index}"
        values.append(companies_payload_json)

        scalar_set_sql = ", ".join(set_clauses)
        scalar_update_sql = (
            f"UPDATE leads SET {scalar_set_sql}, updated_at = NOW()"
            if scalar_set_sql
            else "UPDATE leads SET updated_at = updated_at"
        )

        query = f"""
            WITH locked AS (
                SELECT id
                FROM leads
                WHERE organization_id = $1
                AND id = $2::uuid
                FOR UPDATE
            ),
            input_payloads AS (
                SELECT
                    {contacts_param}::jsonb  AS contacts_payload,
                    {companies_param}::jsonb AS companies_payload
            ),
            l AS (
                {scalar_update_sql}
                WHERE organization_id = $1
                AND id = $2::uuid
                AND EXISTS (SELECT 1 FROM locked)
                RETURNING *
            ),

            desired_contacts AS (
                SELECT
                    (e.value->>'contact_id')::uuid AS contact_id,
                    NULLIF(e.value->>'label', '')::text AS label
                FROM input_payloads p
                CROSS JOIN LATERAL jsonb_array_elements(COALESCE(p.contacts_payload, '[]'::jsonb)) AS e(value)
                WHERE p.contacts_payload IS NOT NULL
            ),
            contacts_deleted AS (
                DELETE FROM lead_contacts lc
                USING input_payloads p
                WHERE p.contacts_payload IS NOT NULL
                AND lc.organization_id = $1
                AND lc.lead_id = $2::uuid
                AND NOT EXISTS (
                    SELECT 1 FROM desired_contacts d WHERE d.contact_id = lc.contact_id
                )
                RETURNING 1
            ),
            contacts_updated AS (
                UPDATE lead_contacts lc
                SET label = d.label, updated_at = NOW()
                FROM desired_contacts d
                WHERE lc.organization_id = $1
                AND lc.lead_id = $2::uuid
                AND lc.contact_id = d.contact_id
                AND lc.label IS DISTINCT FROM d.label
                RETURNING 1
            ),
            contacts_inserted AS (
                INSERT INTO lead_contacts (lead_id, organization_id, contact_id, label)
                SELECT $2::uuid, $1::uuid, d.contact_id, d.label
                FROM desired_contacts d
                WHERE NOT EXISTS (
                    SELECT 1 FROM lead_contacts lc
                    WHERE lc.organization_id = $1
                    AND lc.lead_id = $2::uuid
                    AND lc.contact_id = d.contact_id
                )
                RETURNING 1
            ),

            desired_companies AS (
                SELECT
                    (e.value->>'company_id')::uuid AS company_id,
                    NULLIF(e.value->>'label', '')::text AS label
                FROM input_payloads p
                CROSS JOIN LATERAL jsonb_array_elements(COALESCE(p.companies_payload, '[]'::jsonb)) AS e(value)
                WHERE p.companies_payload IS NOT NULL
            ),
            companies_deleted AS (
                DELETE FROM lead_companies lc
                USING input_payloads p
                WHERE p.companies_payload IS NOT NULL
                AND lc.organization_id = $1
                AND lc.lead_id = $2::uuid
                AND NOT EXISTS (
                    SELECT 1 FROM desired_companies d WHERE d.company_id = lc.company_id
                )
                RETURNING 1
            ),
            companies_updated AS (
                UPDATE lead_companies lc
                SET label = d.label, updated_at = NOW()
                FROM desired_companies d
                WHERE lc.organization_id = $1
                AND lc.lead_id = $2::uuid
                AND lc.company_id = d.company_id
                AND lc.label IS DISTINCT FROM d.label
                RETURNING 1
            ),
            companies_inserted AS (
                INSERT INTO lead_companies (lead_id, organization_id, company_id, label)
                SELECT $2::uuid, $1::uuid, d.company_id, d.label
                FROM desired_companies d
                WHERE NOT EXISTS (
                    SELECT 1 FROM lead_companies lc
                    WHERE lc.organization_id = $1
                    AND lc.lead_id = $2::uuid
                    AND lc.company_id = d.company_id
                )
                RETURNING 1
            ),
            effects AS (
                SELECT
                    (SELECT count(*)::bigint FROM contacts_deleted)  AS cd,
                    (SELECT count(*)::bigint FROM contacts_updated)  AS cu,
                    (SELECT count(*)::bigint FROM contacts_inserted) AS ci,
                    (SELECT count(*)::bigint FROM companies_deleted) AS kcd,
                    (SELECT count(*)::bigint FROM companies_updated) AS kcu,
                    (SELECT count(*)::bigint FROM companies_inserted) AS kci
            )

            SELECT
                l.id,
                l.organization_id,
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
                l.currency,
                l.description,
                l.owner_id,
                l.custom_fields,
                l.created_at,
                l.updated_at,
                CASE
                    WHEN (SELECT companies_payload FROM input_payloads) IS NOT NULL THEN
                        COALESCE(
                            (
                                SELECT json_agg(
                                    json_build_object(
                                        'company_id', d.company_id::text,
                                        'label', d.label,
                                        'company_name', COALESCE(co.name, ''),
                                        'profile_photo_url', co.profile_photo_url
                                    )
                                    ORDER BY coalesce(d.company_id::text, '') ASC
                                )
                                FROM desired_companies d
                                INNER JOIN companies co
                                    ON co.id = d.company_id
                                   AND co.organization_id = $1
                                   AND co.status != '{ClientStatus.DELETED.value}'
                            ),
                            '[]'::json
                        )
                    ELSE
                        ({_LEAD_COMPANIES_AGG_SQL.strip().replace(" AS companies", "")})
                END AS companies,
                CASE
                    WHEN (SELECT contacts_payload FROM input_payloads) IS NOT NULL THEN
                        COALESCE(
                            (
                                SELECT json_agg(
                                    json_build_object(
                                        'contact_id', d.contact_id::text,
                                        'label', d.label,
                                        'contact_name', ({_CONTACT_DISPLAY_NAME_SQL.strip()}),
                                        'profile_photo_url', ct.profile_photo_url,
                                        'addresses', ({_CONTACT_ADDRESSES_JSON_FOR_D_SQL.strip()})
                                    )
                                    ORDER BY coalesce(d.contact_id::text, '') ASC
                                )
                                FROM desired_contacts d
                                INNER JOIN contacts ct
                                    ON ct.id = d.contact_id
                                   AND ct.organization_id = $1
                                   AND ct.status != '{ClientStatus.DELETED.value}'
                            ),
                            '[]'::json
                        )
                    ELSE
                        ({_LEAD_CONTACTS_AGG_SQL.strip().replace(" AS contacts", "")})
                END AS contacts,
                ls.stage_name AS stage_name,
                ({_LEAD_OWNER_DISPLAY_NAME_SQL.strip()}) AS owner_name
            FROM l
            {_LEADS_JOIN_DISPLAY.strip()}
            JOIN effects ON TRUE
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, *values)
        return dict(row) if row else None

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
                l.currency,
                l.description,
                l.owner_id,
                l.custom_fields,
                l.created_at,
                l.updated_at,
                {_LEAD_COMPANIES_AGG_SQL.replace("FROM leads l", "FROM l").strip()},
                {_LEAD_CONTACTS_AGG_SQL.strip()},
                ls.stage_name AS stage_name,
                ({_LEAD_OWNER_DISPLAY_NAME_SQL.replace("l.", "l.").strip()}) AS owner_name
            FROM l
            {_LEADS_JOIN_DISPLAY.strip()}
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, *values)
        if not row:
            return None
        return dict(row)
