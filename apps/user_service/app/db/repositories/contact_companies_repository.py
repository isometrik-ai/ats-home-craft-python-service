"""Contact-company junction persistence (public.contact_companies) — asyncpg."""

from __future__ import annotations

import asyncpg


class ContactCompaniesRepository:
    """Persistence for rows in ``contact_companies`` (contact–company membership)."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Store the asyncpg connection used for all queries in this repository."""
        self.db_connection = db_connection

    async def apply_companies_update_delta(
        self,
        *,
        organization_id: str,
        contact_id: str,
        remove_company_ids: list[str],
        add_company_ids: list[str],
        set_primary_company_ids: list[str],
        unset_primary_company_ids: list[str],
        create_company_name: str | None,
        create_is_primary: bool,
    ) -> str | None:
        """Apply batch add/remove + optional create in a single round trip.

        Notes:
        - Unlinks requested memberships (if present).
        - Clears ``companies.primary_contact_id`` when it matches this contact and the
          company row is being removed from membership.
        - Adds requested memberships (deduped) and optionally sets primary for specified companies.
        - When create_company_name is provided, exactly one company is created and linked.

        Returns:
            str | None: created company id when created, else None
        """
        remove_ids = remove_company_ids or []
        add_ids = add_company_ids or []
        primary_ids = set_primary_company_ids or []
        unset_primary_ids = unset_primary_company_ids or []
        name = (create_company_name or "").strip() or None

        created_id = await self.db_connection.fetchval(
            """
            WITH unset_primary AS (
              UPDATE companies
              SET primary_contact_id = NULL,
                  updated_at = NOW()
              WHERE organization_id = $1::uuid
                AND primary_contact_id = $2::uuid
                AND id = ANY($8::uuid[])
              RETURNING id
            ),
            removed AS (
              DELETE FROM contact_companies
              WHERE organization_id = $1::uuid
                AND contact_id = $2::uuid
                AND company_id = ANY($3::uuid[])
              RETURNING company_id
            ),
            cleared AS (
              UPDATE companies
              SET primary_contact_id = NULL,
                  updated_at = NOW()
              WHERE organization_id = $1::uuid
                AND primary_contact_id = $2::uuid
                AND id = ANY($3::uuid[])
              RETURNING id
            ),
            new_company AS (
              INSERT INTO companies (organization_id, name, primary_contact_id)
              SELECT $1::uuid, $6::text, NULL
              WHERE $6::text IS NOT NULL
              RETURNING id
            ),
            add_ids AS (
              SELECT DISTINCT company_id FROM (
                SELECT unnest($4::uuid[]) AS company_id
                UNION ALL
                SELECT id FROM new_company
              ) s
              WHERE company_id IS NOT NULL
            ),
            added AS (
              INSERT INTO contact_companies (organization_id, contact_id, company_id)
              SELECT $1::uuid, $2::uuid, company_id
              FROM add_ids
              ON CONFLICT (contact_id, company_id) DO NOTHING
              RETURNING 1
            ),
            primary_targets AS (
              SELECT DISTINCT company_id FROM (
                SELECT unnest($5::uuid[]) AS company_id
                UNION ALL
                SELECT id FROM new_company WHERE $7::boolean IS TRUE
              ) s
              WHERE company_id IS NOT NULL
            ),
            primary_set AS (
              UPDATE companies
              SET primary_contact_id = $2::uuid,
                  updated_at = NOW()
              WHERE organization_id = $1::uuid
                AND id IN (SELECT company_id FROM primary_targets)
              RETURNING 1
            )
            SELECT (SELECT id::text FROM new_company)
            """,
            organization_id,
            contact_id,
            remove_ids,
            add_ids,
            primary_ids,
            name,
            bool(create_is_primary),
            unset_primary_ids,
        )
        return str(created_id) if created_id else None

    async def list_distinct_company_ids_for_contacts(
        self,
        *,
        organization_id: str,
        contact_ids: list[str],
    ) -> list[str]:
        """Company ids linked to any of the contacts via ``contact_companies``.

        Returns distinct ids sorted for stable ordering. Empty ``contact_ids`` yields an empty.
        """
        if not contact_ids:
            return []
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT cc.company_id::text AS company_id
            FROM contact_companies cc
            INNER JOIN companies co
              ON co.id = cc.company_id
             AND co.organization_id = cc.organization_id
             AND co.status != 'deleted'
            WHERE cc.organization_id = $1::uuid
              AND cc.contact_id = ANY($2::uuid[])
            ORDER BY company_id
            """,
            organization_id,
            contact_ids,
        )
        return [str(r["company_id"]) for r in rows]

    async def apply_contacts_update_delta(
        self,
        *,
        organization_id: str,
        company_id: str,
        remove_contact_ids: list[str],
        add_contact_ids: list[str],
        set_primary_contact_id: str | None,
        unset_primary_contact_ids: list[str],
    ) -> None:
        """Apply batch add/remove + primary changes for one company in a single round trip.

        Mirrors ``apply_companies_update_delta`` with company/contact roles swapped.

        - Unsets primary when the company's current primary is listed in
          ``unset_primary_contact_ids``.
        - Removes memberships; clears ``companies.primary_contact_id`` when it points
          at a removed contact.
        - Adds memberships (deduped).
        - Sets ``primary_contact_id`` to ``set_primary_contact_id`` when not null
          (caller ensures membership exists).
        """
        remove_ids = remove_contact_ids or []
        add_ids = add_contact_ids or []
        unset_ids = unset_primary_contact_ids or []

        await self.db_connection.execute(
            """
            WITH unset_primary AS (
              UPDATE companies
              SET primary_contact_id = NULL,
                  updated_at = NOW()
              WHERE organization_id = $1::uuid
                AND id = $2::uuid
                AND primary_contact_id = ANY($6::uuid[])
              RETURNING id
            ),
            removed AS (
              DELETE FROM contact_companies
              WHERE organization_id = $1::uuid
                AND company_id = $2::uuid
                AND contact_id = ANY($3::uuid[])
              RETURNING contact_id
            ),
            cleared AS (
              UPDATE companies
              SET primary_contact_id = NULL,
                  updated_at = NOW()
              WHERE organization_id = $1::uuid
                AND id = $2::uuid
                AND primary_contact_id = ANY($3::uuid[])
              RETURNING id
            ),
            added AS (
              INSERT INTO contact_companies (organization_id, contact_id, company_id)
              SELECT $1::uuid, contact_uuid, $2::uuid
              FROM unnest($4::uuid[]) AS unpack(contact_uuid)
              ON CONFLICT (contact_id, company_id) DO NOTHING
              RETURNING 1
            ),
            primary_set AS (
              UPDATE companies
              SET primary_contact_id = $5::uuid,
                  updated_at = NOW()
              WHERE organization_id = $1::uuid
                AND id = $2::uuid
                AND $5::uuid IS NOT NULL
              RETURNING 1
            )
            SELECT 1
            """,
            organization_id,
            company_id,
            remove_ids,
            add_ids,
            set_primary_contact_id,
            unset_ids,
        )

    async def get_contact_companies_snapshot(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> list[object]:
        """Fetch the latest companies snapshot for a contact.

        Returns the same JSON shape as `apply_companies_update_delta(...)->companies`.
        """
        row = await self.db_connection.fetchrow(
            """
            SELECT COALESCE(
              jsonb_agg(
                jsonb_build_object(
                  'company_id', cc.company_id::text,
                  'name',       co.name,
                  'industry',   co.industry,
                  'is_primary', COALESCE((co.primary_contact_id = $2::uuid), FALSE)
                )
                ORDER BY COALESCE(co.name, ''), cc.company_id::text
              ),
              '[]'::jsonb
            ) AS companies
            FROM contact_companies cc
            LEFT JOIN companies co
              ON co.id = cc.company_id
             AND co.organization_id = $1::uuid
             AND co.status != 'deleted'
            WHERE cc.organization_id = $1::uuid
              AND cc.contact_id = $2::uuid;
            """,
            organization_id,
            contact_id,
        )
        if not row:
            return []
        return row["companies"] or []

    async def get_company_contacts_snapshot(
        self,
        *,
        organization_id: str,
        company_id: str,
    ) -> list[object]:
        """Fetch the latest contacts snapshot for a company.

        Returns the same JSON shape as `CompaniesRepository.get_company_details(...)->contacts`.
        """
        primary_contact_id = await self.db_connection.fetchval(
            """
            SELECT primary_contact_id
            FROM companies
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
              AND status != 'deleted'
            """,
            organization_id,
            company_id,
        )

        row = await self.db_connection.fetchrow(
            """
            SELECT COALESCE(
              jsonb_agg(
                jsonb_build_object(
                  'id',         ct.id::text,
                  'first_name', ct.first_name,
                  'last_name',  ct.last_name,
                  'title',      ct.title,
                  'email',      NULLIF(au.email::text, ''),
                  'phones',     COALESCE(ct.phones, '[]'::jsonb),
                  'is_primary', COALESCE(($3::uuid = ct.id), FALSE)
                )
                ORDER BY COALESCE(($3::uuid = ct.id), FALSE) DESC,
                         ct.created_at ASC
              ),
              '[]'::jsonb
            ) AS contacts
            FROM contact_companies cc
            INNER JOIN contacts ct
              ON ct.id = cc.contact_id
             AND ct.organization_id = $1::uuid
             AND ct.status != 'deleted'
            LEFT JOIN auth.users au
              ON au.id = ct.user_id
            WHERE cc.organization_id = $1::uuid
              AND cc.company_id = $2::uuid;
            """,
            organization_id,
            company_id,
            primary_contact_id,
        )
        if not row:
            return []
        return row["contacts"] or []
