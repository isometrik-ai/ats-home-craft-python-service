"""Contact-company junction persistence (public.contact_companies) — asyncpg."""

from __future__ import annotations

import asyncpg


class ContactCompaniesRepository:
    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    async def link_contact_to_company(
        self,
        *,
        organization_id: str,
        contact_id: str,
        company_id: str,
    ) -> None:
        """Link a contact to a company."""
        await self.db_connection.execute(
            """
            INSERT INTO contact_companies (organization_id, contact_id, company_id)
            VALUES ($1::uuid, $2::uuid, $3::uuid)
            ON CONFLICT (contact_id, company_id) DO NOTHING
            """,
            organization_id,
            contact_id,
            company_id,
        )

    async def link_contact_to_company_and_optionally_set_primary(
        self,
        *,
        organization_id: str,
        contact_id: str,
        company_id: str,
        set_as_primary: bool,
    ) -> None:
        """Link contact membership and (optionally) set as company primary in one round trip."""
        await self.db_connection.execute(
            """
            WITH membership AS (
              INSERT INTO contact_companies (organization_id, contact_id, company_id)
              VALUES ($1::uuid, $2::uuid, $3::uuid)
              ON CONFLICT (contact_id, company_id) DO NOTHING
              RETURNING 1
            )
            UPDATE companies
            SET primary_contact_id = $2::uuid,
                updated_at = NOW()
            WHERE id = $3::uuid
              AND organization_id = $1::uuid
              AND $4::boolean IS TRUE
            """,
            organization_id,
            contact_id,
            company_id,
            bool(set_as_primary),
        )

    async def replace_contact_company_membership_and_optionally_set_primary(
        self,
        *,
        organization_id: str,
        contact_id: str,
        old_company_id: str,
        new_company_id: str,
        set_as_primary: bool,
    ) -> None:
        """Replace membership old->new and (optionally) set new company primary in one round trip.

        Also clears old company's primary_contact_id when it currently points to this contact.
        """
        await self.db_connection.execute(
            """
            WITH removed AS (
              DELETE FROM contact_companies
              WHERE organization_id = $1::uuid
                AND contact_id = $2::uuid
                AND company_id = $3::uuid
              RETURNING 1
            ),
            cleared AS (
              UPDATE companies
              SET primary_contact_id = NULL,
                  updated_at = NOW()
              WHERE id = $3::uuid
                AND organization_id = $1::uuid
                AND primary_contact_id = $2::uuid
              RETURNING 1
            ),
            added AS (
              INSERT INTO contact_companies (organization_id, contact_id, company_id)
              VALUES ($1::uuid, $2::uuid, $4::uuid)
              ON CONFLICT (contact_id, company_id) DO NOTHING
              RETURNING 1
            )
            UPDATE companies
            SET primary_contact_id = $2::uuid,
                updated_at = NOW()
            WHERE id = $4::uuid
              AND organization_id = $1::uuid
              AND $5::boolean IS TRUE
            """,
            organization_id,
            contact_id,
            old_company_id,
            new_company_id,
            bool(set_as_primary),
        )

    async def create_company_and_attach_contact_and_optionally_set_primary(
        self,
        *,
        organization_id: str,
        contact_id: str,
        company_name: str,
        set_as_primary: bool,
    ) -> str:
        """Create company + attach membership + (optional) set primary in one round trip.

        Returns:
            str: newly created company_id
        """
        company_id = await self.db_connection.fetchval(
            """
            WITH co AS (
              INSERT INTO companies (organization_id, name, primary_contact_id)
              VALUES ($1::uuid, $3::text, NULL)
              RETURNING id
            ),
            membership AS (
              INSERT INTO contact_companies (organization_id, contact_id, company_id)
              SELECT $1::uuid, $2::uuid, co.id
              FROM co
              ON CONFLICT (contact_id, company_id) DO NOTHING
              RETURNING 1
            ),
            primary_set AS (
              UPDATE companies
              SET primary_contact_id = $2::uuid,
                  updated_at = NOW()
              WHERE id = (SELECT id FROM co)
                AND organization_id = $1::uuid
                AND $4::boolean IS TRUE
              RETURNING 1
            )
            SELECT (SELECT id FROM co)::text
            """,
            organization_id,
            contact_id,
            company_name,
            bool(set_as_primary),
        )
        return str(company_id)

    async def replace_with_new_company_name_and_optionally_set_primary(
        self,
        *,
        organization_id: str,
        contact_id: str,
        old_company_id: str,
        new_company_name: str,
        set_as_primary: bool,
    ) -> str:
        """Replace old membership with a newly created company in one round trip.

        Creates company, removes old membership, clears old primary if it matches,
        links membership to new company, and optionally sets new company primary.

        Returns:
            str: newly created company_id
        """
        company_id = await self.db_connection.fetchval(
            """
            WITH co AS (
              INSERT INTO companies (organization_id, name, primary_contact_id)
              VALUES ($1::uuid, $4::text, NULL)
              RETURNING id
            ),
            removed AS (
              DELETE FROM contact_companies
              WHERE organization_id = $1::uuid
                AND contact_id = $2::uuid
                AND company_id = $3::uuid
              RETURNING 1
            ),
            cleared AS (
              UPDATE companies
              SET primary_contact_id = NULL,
                  updated_at = NOW()
              WHERE id = $3::uuid
                AND organization_id = $1::uuid
                AND primary_contact_id = $2::uuid
              RETURNING 1
            ),
            membership AS (
              INSERT INTO contact_companies (organization_id, contact_id, company_id)
              SELECT $1::uuid, $2::uuid, co.id
              FROM co
              ON CONFLICT (contact_id, company_id) DO NOTHING
              RETURNING 1
            ),
            primary_set AS (
              UPDATE companies
              SET primary_contact_id = $2::uuid,
                  updated_at = NOW()
              WHERE id = (SELECT id FROM co)
                AND organization_id = $1::uuid
                AND $5::boolean IS TRUE
              RETURNING 1
            )
            SELECT (SELECT id FROM co)::text
            """,
            organization_id,
            contact_id,
            old_company_id,
            new_company_name,
            bool(set_as_primary),
        )
        return str(company_id)

    async def unlink_contact_from_company(
        self,
        *,
        organization_id: str,
        contact_id: str,
        company_id: str,
    ) -> None:
        """Unlink a contact from a company."""
        await self.db_connection.execute(
            """
            DELETE FROM contact_companies
            WHERE organization_id = $1::uuid AND contact_id = $2::uuid AND company_id = $3::uuid
            """,
            organization_id,
            contact_id,
            company_id,
        )

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
        - Clears `companies.primary_contact_id` only when it matches this contact and the company is being removed.
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

        - Unsets primary when the company's current primary is listed in ``unset_primary_contact_ids``.
        - Removes memberships; clears ``companies.primary_contact_id`` when it points at a removed contact.
        - Adds memberships (deduped).
        - Sets ``primary_contact_id`` to ``set_primary_contact_id`` when not null (caller ensures membership).
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
              SELECT $1::uuid, cid, $2::uuid
              FROM unnest($4::uuid[]) AS t(cid)
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

    async def is_contact_member_of_company(
        self,
        *,
        organization_id: str,
        contact_id: str,
        company_id: str,
    ) -> bool:
        """Check if a contact is a member of a company."""
        val = await self.db_connection.fetchval(
            """
            SELECT EXISTS(
              SELECT 1
              FROM contact_companies
              WHERE organization_id = $1::uuid
                AND contact_id = $2::uuid
                AND company_id = $3::uuid
            )
            """,
            organization_id,
            contact_id,
            company_id,
        )
        return bool(val)

    async def list_company_contacts(
        self,
        *,
        organization_id: str,
        company_id: str,
    ) -> list[dict]:
        """List all contacts for a company."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              ct.id,
              ct.first_name,
              ct.last_name,
              ct.title,
              NULLIF(au.email::text, '') AS email,
              (co.primary_contact_id = ct.id) AS is_primary
            FROM contact_companies cc
            INNER JOIN contacts ct ON ct.id = cc.contact_id
            LEFT JOIN auth.users au
              ON au.id = ct.user_id
            INNER JOIN companies co ON co.id = cc.company_id
            WHERE cc.organization_id = $1::uuid
              AND cc.company_id = $2::uuid
              AND ct.status != 'deleted'
              AND co.status != 'deleted'
            ORDER BY ct.created_at ASC
            """,
            organization_id,
            company_id,
        )
        return [dict(r) for r in rows]
