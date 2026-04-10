"""Contacts persistence (public.contacts, public.contact_addresses) — asyncpg."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ClientStatus
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode

CONTACT_JSONB_COLUMNS: frozenset[str] = frozenset(
    {
        "phones",
        "custom_fields",
        "additional_data",
        "social_pages",
        "work_history",
        "educational_history",
    }
)

CONTACT_ADDRESS_JSONB_COLUMNS: frozenset[str] = frozenset({"address_data"})


class ContactsRepository(BaseRepository):
    """Database operations class for contact management using asyncpg."""
    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection."""
        super().__init__(db_connection=db_connection)

    async def get_contact_id_by_email(self, *, organization_id: str, email: str) -> str | None:
        """Return an existing contact id for email within an organization (case-insensitive)."""
        email_norm = (email or "").strip().lower()
        if not email_norm:
            return None
        row = await self.db_connection.fetchrow(
            """
            SELECT ct.id::text AS id
            FROM contacts ct
            LEFT JOIN auth.users au
              ON au.id = ct.user_id
            WHERE ct.organization_id = $1::uuid
              AND ct.status != $2
              AND LOWER(COALESCE(au.email::text, '')) = $3
            LIMIT 1
            """,
            organization_id,
            ClientStatus.DELETED.value,
            email_norm,
        )
        return str(row["id"]) if row and row.get("id") else None

    async def create_contacts(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create contacts in bulk."""
        required = ["organization_id"]
        optional = [
            "user_id",
            "isometrik_user_id",
            "status",
            "prefix",
            "first_name",
            "middle_name",
            "last_name",
            "title",
            "date_of_birth",
            "profile_photo_url",
            "phones",
            "tags",
            "custom_fields",
            "additional_data",
            "social_pages",
            "description",
            "work_history",
            "educational_history",
            "skills",
            "enrichment_done",
            "enrichment_status",
            "last_enriched_at",
        ]
        return await self.bulk_insert_returning(
            table="contacts",
            required_columns=required,
            optional_columns=optional,
            rows=rows,
            jsonb_columns=CONTACT_JSONB_COLUMNS,
        )

    async def create_contact_with_optional_company_link(
        self,
        *,
        organization_id: str,
        contact_data: dict[str, Any],
        company_id: str | None,
        company_name: str | None,
        make_primary: bool,
    ) -> dict[str, Any]:
        """Create contact and optionally link to a company in one DB round trip.

        - If `company_id` is provided: link to that company.
        - Else if `company_name` is provided: create company and link to it.
        - If `make_primary` is true and a company is selected: set contact as primary.

        Returns:
            dict with keys:
            - contact_id (str)
            - company_id (str | None)
            - contact (dict | None): inserted contact row
        """
        row = await self.db_connection.fetchrow(
            """
            WITH new_company AS (
              INSERT INTO companies (organization_id, name, primary_contact_id)
              SELECT $1::uuid, $3::text, NULL
              WHERE $3::text IS NOT NULL AND $2::uuid IS NULL
              RETURNING id
            ),
            company AS (
              SELECT COALESCE($2::uuid, (SELECT id FROM new_company)) AS id
            ),
            contact AS (
              INSERT INTO contacts (
                organization_id,
                user_id,
                isometrik_user_id,
                status,
                prefix,
                first_name,
                middle_name,
                last_name,
                title,
                date_of_birth,
                profile_photo_url,
                phones,
                tags,
                custom_fields,
                additional_data,
                social_pages
              )
              VALUES (
                $1::uuid,
                $4::uuid,
                $5::text,
                $6::text,
                $7::text,
                $8::text,
                $9::text,
                $10::text,
                $11::text,
                $12::date,
                $13::text,
                $14::jsonb,
                $15::text[],
                $16::jsonb,
                $17::jsonb,
                $18::jsonb
              )
              RETURNING *
            ),
            membership AS (
              INSERT INTO contact_companies (organization_id, contact_id, company_id)
              SELECT $1::uuid, (SELECT id FROM contact), (SELECT id FROM company)
              WHERE (SELECT id FROM company) IS NOT NULL
              ON CONFLICT (contact_id, company_id) DO NOTHING
              RETURNING 1
            ),
            primary_set AS (
              UPDATE companies
              SET primary_contact_id = (SELECT id FROM contact),
                  updated_at = NOW()
              WHERE id = (SELECT id FROM company)
                AND organization_id = $1::uuid
                AND $19::boolean IS TRUE
                AND (SELECT id FROM company) IS NOT NULL
              RETURNING 1
            )
            SELECT
              (SELECT id FROM contact)::text AS contact_id,
              (SELECT id FROM company)::text AS company_id,
              (SELECT to_jsonb(contact) FROM contact) AS contact
            """,
            organization_id,
            company_id,
            company_name,
            contact_data.get("user_id"),
            contact_data.get("isometrik_user_id"),
            contact_data.get("status"),
            contact_data.get("prefix"),
            contact_data.get("first_name"),
            contact_data.get("middle_name"),
            contact_data.get("last_name"),
            contact_data.get("title"),
            contact_data.get("date_of_birth"),
            contact_data.get("profile_photo_url"),
            contact_data.get("phones"),
            contact_data.get("tags"),
            contact_data.get("custom_fields"),
            contact_data.get("additional_data"),
            contact_data.get("social_pages"),
            bool(make_primary),
        )
        if not row:
            return {"contact_id": None, "company_id": None, "contact": None}
        contact_row = row.get("contact")
        return {
            "contact_id": str(row["contact_id"]),
            "company_id": row["company_id"],
            "contact": dict(contact_row) if isinstance(contact_row, dict) else contact_row,
        }

    async def filter_contact_ids_in_organization(
        self, *, organization_id: str, contact_ids: list[str]
    ) -> set[str]:
        """Return the subset of contact ids that exist and are not deleted in the organization."""
        if not contact_ids:
            return set()
        rows = await self.db_connection.fetch(
            """
            SELECT id::text AS id
            FROM contacts
            WHERE organization_id = $1::uuid
              AND id = ANY($2::uuid[])
              AND status <> $3
            """,
            organization_id,
            contact_ids,
            ClientStatus.DELETED.value,
        )
        return {str(r["id"]) for r in rows}

    async def get_contact_for_update(self, *, contact_id: str, organization_id: str) -> dict | None:
        """Get a contact for update."""
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM contacts
            WHERE id = $1::uuid AND organization_id = $2::uuid AND status != $3
            FOR UPDATE
            """,
            contact_id,
            organization_id,
            ClientStatus.DELETED.value,
        )
        return dict(row) if row else None

    async def update_contact(
        self,
        *,
        contact_id: str,
        organization_id: str,
        update_data: dict[str, Any],
    ) -> dict | None:
        """Update a contact."""
        return await self.update_returning(
            table="contacts",
            where_sql="WHERE id = $%d::uuid AND organization_id = $%d::uuid AND status != $%d"
            % (len(update_data) + 1, len(update_data) + 2, len(update_data) + 3),
            where_params=[contact_id, organization_id, ClientStatus.DELETED.value],
            update_data=update_data,
            jsonb_columns=CONTACT_JSONB_COLUMNS,
            touch_updated_at=True,
        )

    async def soft_delete_contact(self, *, contact_id: str, organization_id: str) -> dict[str, Any]:
        """Soft delete a contact and return the updated row."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE contacts
            SET status = $3, updated_at = NOW()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND status != $3
            RETURNING *
            """,
            contact_id,
            organization_id,
            ClientStatus.DELETED.value,
        )
        if not row:
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return dict(row)

    async def create_contact_addresses(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create contact addresses in bulk."""
        required = ["contact_id"]
        optional = [
            "place_id",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "country",
            "latitude",
            "longitude",
            "address_type",
            "address_data",
            "is_primary",
        ]
        return await self.bulk_insert_returning(
            table="contact_addresses",
            required_columns=required,
            optional_columns=optional,
            rows=rows,
            jsonb_columns=CONTACT_ADDRESS_JSONB_COLUMNS,
        )

    async def update_contact_address(
        self,
        *,
        contact_id: str,
        address_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update a single contact address row by id (scoped to contact_id)."""
        if not update_data:
            return None
        return await self.update_returning(
            table="contact_addresses",
            where_sql=f"WHERE id = ${len(update_data) + 1}::uuid AND contact_id = ${len(update_data) + 2}::uuid",
            where_params=[address_id, contact_id],
            update_data=update_data,
            jsonb_columns=CONTACT_ADDRESS_JSONB_COLUMNS,
            touch_updated_at=True,
        )

    async def delete_contact_addresses(
        self,
        *,
        contact_id: str,
        address_ids: list[str],
    ) -> None:
        """Delete contact address rows by ids (scoped to contact_id)."""
        if not address_ids:
            return
        await self.db_connection.execute(
            """
            DELETE FROM contact_addresses
            WHERE contact_id = $1::uuid
              AND id = ANY($2::uuid[])
            """,
            contact_id,
            address_ids,
        )

    async def get_contact_addresses(self, *, contact_id: str) -> list[dict[str, Any]]:
        """Get contact addresses."""
        rows = await self.db_connection.fetch(
            """
            SELECT *
            FROM contact_addresses
            WHERE contact_id = $1::uuid
            ORDER BY is_primary DESC, created_at ASC
            """,
            contact_id,
        )
        return [dict(r) for r in rows]

    async def get_contact_details(
        self,
        *,
        contact_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Get a contact + linked companies + addresses in one round trip."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              ct.*,
              COALESCE(companies.companies, '[]'::jsonb) AS companies,
              COALESCE(addresses.addresses, '[]'::jsonb) AS addresses
            FROM contacts ct
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(
                jsonb_build_object(
                  'company_id', co.id::text,
                  'name',       co.name,
                  'industry',   co.industry,
                  'is_primary', (co.primary_contact_id = ct.id)
                )
                ORDER BY co.name
              ) FILTER (WHERE co.id IS NOT NULL) AS companies
              FROM contact_companies cc
              INNER JOIN companies co
                ON co.id = cc.company_id
               AND co.organization_id = ct.organization_id
               AND co.status != 'deleted'
              WHERE cc.organization_id = ct.organization_id
                AND cc.contact_id = ct.id
            ) companies ON TRUE
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(to_jsonb(a) ORDER BY a.is_primary DESC, a.created_at ASC) AS addresses
              FROM contact_addresses a
              WHERE a.contact_id = ct.id
            ) addresses ON TRUE
            WHERE ct.id = $1::uuid
              AND ct.organization_id = $2::uuid
              AND ct.status != $3
            """,
            contact_id,
            organization_id,
            ClientStatus.DELETED.value,
        )
        if not row:
            return None
        result = dict(row)
        # asyncpg may return jsonb as str in some configurations; normalize defensively.
        for key in ("companies", "addresses"):
            val = result.get(key)
            if isinstance(val, str):
                try:
                    result[key] = json.loads(val)
                except Exception:
                    result[key] = []
        return result

    async def list_contacts(
        self,
        *,
        organization_id: str,
        search: str | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List contacts with simple search (first/last/email) and pagination."""
        offset = (page - 1) * page_size
        args: list[Any] = [organization_id, ClientStatus.DELETED.value]
        where = ["organization_id = $1::uuid", "status != $2"]
        idx = 3
        if status:
            where.append(f"status = ${idx}")
            args.append(status)
            idx += 1
        if search:
            where.append(
                f"(COALESCE(first_name,'') || ' ' || COALESCE(last_name,'') ILIKE ${idx} OR COALESCE(au.email::text,'') ILIKE ${idx})"
            )
            args.append(f"%{search.strip()}%")
            idx += 1
        where_sql = " AND ".join(where)
        total = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(1)
            FROM contacts ct
            LEFT JOIN auth.users au ON au.id = ct.user_id
            WHERE {where_sql}
            """,
            *args,
        )
        rows = await self.db_connection.fetch(
            f"""
            WITH company_names_by_contact AS (
              SELECT
                cc.contact_id,
                jsonb_agg(co.name ORDER BY co.name) FILTER (WHERE co.id IS NOT NULL) AS company_names
              FROM contact_companies cc
              INNER JOIN companies co
                ON co.id = cc.company_id
               AND co.organization_id = $1::uuid
               AND co.status != 'deleted'
              WHERE cc.organization_id = $1::uuid
              GROUP BY cc.contact_id
            )
            SELECT
              ct.id::text AS id,
              ct.organization_id::text AS organization_id,
              ct.status,
              ct.first_name,
              ct.last_name,
              ct.title,
              au.email::text AS email,
              ct.profile_photo_url,
              ct.phones,
              COALESCE(cn.company_names, '[]'::jsonb) AS company_names,
              ct.created_at,
              ct.updated_at
            FROM contacts ct
            LEFT JOIN auth.users au ON au.id = ct.user_id
            LEFT JOIN company_names_by_contact cn
              ON cn.contact_id = ct.id
            WHERE {where_sql}
            ORDER BY ct.created_at DESC
            OFFSET ${idx} LIMIT ${idx + 1}
            """,
            *(args + [offset, page_size]),
        )
        return [dict(r) for r in rows], int(total or 0)
