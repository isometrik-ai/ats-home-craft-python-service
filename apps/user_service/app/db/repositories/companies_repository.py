"""Companies persistence (public.companies, public.company_addresses) — asyncpg."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ClientStatus
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode

COMPANY_JSONB_COLUMNS: frozenset[str] = frozenset(
    {
        "websites",
        "billing_preferences",
        "custom_fields",
        "additional_data",
        "social_pages",
        "linked_pages",
        "products",
        "key_people",
        "sales_intelligence",
    }
)

COMPANY_ADDRESS_JSONB_COLUMNS: frozenset[str] = frozenset({"address_data"})


class CompaniesRepository(BaseRepository):
    def __init__(self, db_connection: asyncpg.Connection) -> None:
        super().__init__(db_connection=db_connection)

    async def create_company_with_optional_contact_link(
        self,
        *,
        organization_id: str,
        company_data: dict[str, Any],
        addresses: list[dict[str, Any]] | None,
        contact_id: str | None,
        contact_data: dict[str, Any] | None = None,
        contact_addresses: list[dict[str, Any]] | None = None,
        set_primary: bool,
    ) -> dict[str, Any]:
        """Create company + optional addresses + optional contact (existing or created) + optional primary in one trip.

        Returns:
            dict with keys:
            - company_id (str)
            - company (dict): inserted company row
            - contact_id (str | None): linked/created contact id (if requested)
            - contact (dict | None): inserted contact row (only when created inline)
            - contact_found (bool): whether the provided contact_id exists in org and not deleted
        """
        addresses_payload = addresses or []
        # company_id is available only after company insert, so we inject it in SQL.
        addresses_json = json.dumps(addresses_payload) if addresses_payload else None
        contact_addresses_payload = contact_addresses or []
        contact_addresses_json = json.dumps(contact_addresses_payload) if contact_addresses_payload else None

        if contact_id is not None and contact_data is not None:
            raise ValueError("Provide only one of contact_id or contact_data.")

        row = await self.db_connection.fetchrow(
            """
            WITH contact_exists AS (
              SELECT ct.id
              FROM contacts ct
              WHERE $19::uuid IS NOT NULL
                AND ct.id = $19::uuid
                AND ct.organization_id = $1::uuid
                AND ct.status != $20::text
            ),
            new_contact AS (
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
              SELECT
                $1::uuid,
                c.user_id,
                c.isometrik_user_id,
                c.status,
                c.prefix,
                c.first_name,
                c.middle_name,
                c.last_name,
                c.title,
                c.date_of_birth,
                c.profile_photo_url,
                c.phones,
                c.tags,
                c.custom_fields,
                c.additional_data,
                c.social_pages
              FROM jsonb_to_recordset(COALESCE($21::jsonb, '[]'::jsonb)) AS c(
                user_id uuid,
                isometrik_user_id text,
                status text,
                prefix text,
                first_name text,
                middle_name text,
                last_name text,
                title text,
                date_of_birth date,
                profile_photo_url text,
                phones jsonb,
                tags text[],
                custom_fields jsonb,
                additional_data jsonb,
                social_pages jsonb
              )
              WHERE $19::uuid IS NULL
              RETURNING *
            ),
            contact AS (
              SELECT
                COALESCE(
                  (SELECT id FROM contact_exists),
                  (SELECT id FROM new_contact)
                ) AS id
            ),
            company AS (
              INSERT INTO companies (
                organization_id,
                primary_contact_id,
                status,
                name,
                industry,
                profile_photo_url,
                portal_access,
                tags,
                websites,
                billing_preferences,
                social_pages,
                target_market_segments,
                current_tech_stack,
                preferred_communication_channels,
                industry_specific_terminologies,
                description,
                custom_fields,
                additional_data
              )
              VALUES (
                $1::uuid,
                CASE WHEN $23::boolean IS TRUE THEN (SELECT id FROM contact) ELSE NULL END,
                $2::text,
                $3::text,
                $4::text,
                $5::text,
                $6::boolean,
                $7::text[],
                $8::jsonb,
                $9::jsonb,
                $10::jsonb,
                $11::text[],
                $12::text[],
                $13::text[],
                $14::text[],
                $15::text,
                $16::jsonb,
                $17::jsonb
              )
              RETURNING *
            ),
            inserted_addresses AS (
              INSERT INTO company_addresses (
                company_id,
                place_id,
                address_line1,
                address_line2,
                city,
                state,
                postal_code,
                country,
                latitude,
                longitude,
                address_type,
                address_data,
                is_primary
              )
              SELECT
                (SELECT id FROM company) AS company_id,
                a.place_id,
                a.address_line1,
                a.address_line2,
                a.city,
                a.state,
                a.postal_code,
                a.country,
                a.latitude,
                a.longitude,
                a.address_type,
                COALESCE(a.address_data, '{}'::jsonb) AS address_data,
                a.is_primary
              FROM jsonb_to_recordset(COALESCE($18::jsonb, '[]'::jsonb)) AS a(
                place_id text,
                address_line1 text,
                address_line2 text,
                city text,
                state text,
                postal_code text,
                country text,
                latitude double precision,
                longitude double precision,
                address_type text,
                address_data jsonb,
                is_primary boolean
              )
              RETURNING 1
            ),
            inserted_contact_addresses AS (
              INSERT INTO contact_addresses (
                contact_id,
                place_id,
                address_line1,
                address_line2,
                city,
                state,
                postal_code,
                country,
                latitude,
                longitude,
                address_type,
                address_data,
                is_primary
              )
              SELECT
                (SELECT id FROM contact) AS contact_id,
                a.place_id,
                a.address_line1,
                a.address_line2,
                a.city,
                a.state,
                a.postal_code,
                a.country,
                a.latitude,
                a.longitude,
                a.address_type,
                COALESCE(a.address_data, '{}'::jsonb) AS address_data,
                a.is_primary
              FROM jsonb_to_recordset(COALESCE($22::jsonb, '[]'::jsonb)) AS a(
                place_id text,
                address_line1 text,
                address_line2 text,
                city text,
                state text,
                postal_code text,
                country text,
                latitude double precision,
                longitude double precision,
                address_type text,
                address_data jsonb,
                is_primary boolean
              )
              WHERE (SELECT id FROM contact) IS NOT NULL
                AND (SELECT id FROM new_contact) IS NOT NULL
              RETURNING 1
            ),
            membership AS (
              INSERT INTO contact_companies (organization_id, contact_id, company_id)
              SELECT $1::uuid, (SELECT id FROM contact), (SELECT id FROM company)
              WHERE (SELECT id FROM contact) IS NOT NULL
              ON CONFLICT (contact_id, company_id) DO NOTHING
              RETURNING contact_id, company_id
            )
            SELECT
              (SELECT id::text FROM company) AS company_id,
              (SELECT to_jsonb(co) FROM companies AS co WHERE co.id = (SELECT id FROM company)) AS company,
              (SELECT id::text FROM contact) AS contact_id,
              (SELECT to_jsonb(new_contact) FROM new_contact) AS contact,
              (SELECT EXISTS(SELECT 1 FROM contact_exists)) AS contact_found
            """,
            organization_id,
            company_data.get("status"),
            company_data.get("name"),
            company_data.get("industry"),
            company_data.get("profile_photo_url"),
            company_data.get("portal_access"),
            company_data.get("tags"),
            company_data.get("websites"),
            company_data.get("billing_preferences"),
            company_data.get("social_pages"),
            company_data.get("target_market_segments"),
            company_data.get("current_tech_stack"),
            company_data.get("preferred_communication_channels"),
            company_data.get("industry_specific_terminologies"),
            company_data.get("description"),
            company_data.get("custom_fields"),
            company_data.get("additional_data"),
            addresses_json,
            contact_id,
            ClientStatus.DELETED.value,
            json.dumps([contact_data]) if contact_data else None,
            contact_addresses_json,
            bool(set_primary),
        )
        if not row:
            return {
                "company_id": None,
                "company": None,
                "contact_id": None,
                "contact": None,
                "contact_found": False,
            }
        company_row = row.get("company")
        contact_row = row.get("contact")
        return {
            "company_id": str(row["company_id"]),
            "company": dict(company_row) if isinstance(company_row, dict) else company_row,
            "contact_id": row.get("contact_id"),
            "contact": dict(contact_row) if isinstance(contact_row, dict) else contact_row,
            "contact_found": bool(row.get("contact_found")),
        }

    async def get_company_for_update(self, *, company_id: str, organization_id: str) -> dict | None:
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM companies
            WHERE id = $1::uuid AND organization_id = $2::uuid AND status != $3
            FOR UPDATE
            """,
            company_id,
            organization_id,
            ClientStatus.DELETED.value,
        )
        return dict(row) if row else None

    async def update_company(
        self,
        *,
        company_id: str,
        organization_id: str,
        update_data: dict[str, Any],
    ) -> dict | None:
        return await self.update_returning(
            table="companies",
            where_sql="WHERE id = $%d::uuid AND organization_id = $%d::uuid AND status != $%d"
            % (len(update_data) + 1, len(update_data) + 2, len(update_data) + 3),
            where_params=[company_id, organization_id, ClientStatus.DELETED.value],
            update_data=update_data,
            jsonb_columns=COMPANY_JSONB_COLUMNS,
            touch_updated_at=True,
        )

    async def soft_delete_company(self, *, company_id: str, organization_id: str) -> dict[str, Any]:
        row = await self.db_connection.fetchrow(
            """
            UPDATE companies
            SET status = $3, updated_at = NOW()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND status != $3
            RETURNING *
            """,
            company_id,
            organization_id,
            ClientStatus.DELETED.value,
        )
        if not row:
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return dict(row)

    async def create_company_addresses(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        required = ["company_id"]
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
            table="company_addresses",
            required_columns=required,
            optional_columns=optional,
            rows=rows,
            jsonb_columns=COMPANY_ADDRESS_JSONB_COLUMNS,
        )

    async def update_company_address(
        self,
        *,
        company_id: str,
        address_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update a single company address row by id (scoped to company_id)."""
        if not update_data:
            return None
        return await self.update_returning(
            table="company_addresses",
            where_sql="WHERE id = $%d::uuid AND company_id = $%d::uuid"
            % (len(update_data) + 1, len(update_data) + 2),
            where_params=[address_id, company_id],
            update_data=update_data,
            jsonb_columns=COMPANY_ADDRESS_JSONB_COLUMNS,
            touch_updated_at=True,
        )

    async def delete_company_addresses(
        self,
        *,
        company_id: str,
        address_ids: list[str],
    ) -> None:
        """Delete company address rows by ids (scoped to company_id)."""
        if not address_ids:
            return
        await self.db_connection.execute(
            """
            DELETE FROM company_addresses
            WHERE company_id = $1::uuid
              AND id = ANY($2::uuid[])
            """,
            company_id,
            address_ids,
        )

    async def get_company_details(
        self,
        *,
        company_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Get a company + primary contact + member contacts + addresses in one round trip."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              co.*,
              COALESCE(primary_contact.primary_contact, NULL) AS primary_contact,
              COALESCE(contacts.contacts, '[]'::jsonb) AS contacts,
              COALESCE(addresses.addresses, '[]'::jsonb) AS addresses
            FROM companies co
            LEFT JOIN LATERAL (
              SELECT jsonb_build_object(
                'id',         ct.id::text,
                'first_name', ct.first_name,
                'last_name',  ct.last_name,
                'title',      ct.title,
                'email',      NULLIF(au.email::text, ''),
                'phones',     ct.phones
              ) AS primary_contact
              FROM contacts ct
              LEFT JOIN auth.users au
                ON au.id = ct.user_id
              WHERE ct.id = co.primary_contact_id
                AND ct.organization_id = co.organization_id
                AND ct.status != 'deleted'
            ) primary_contact ON TRUE
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(
                jsonb_build_object(
                  'id',         ct.id::text,
                  'first_name', ct.first_name,
                  'last_name',  ct.last_name,
                  'title',      ct.title,
                  'email',      NULLIF(au.email::text, ''),
                  'phones',     ct.phones,
                  'is_primary', (co.primary_contact_id = ct.id)
                )
                ORDER BY ct.created_at ASC
              ) FILTER (WHERE ct.id IS NOT NULL) AS contacts
              FROM contact_companies cc
              INNER JOIN contacts ct
                ON ct.id = cc.contact_id
               AND ct.organization_id = co.organization_id
               AND ct.status != 'deleted'
              LEFT JOIN auth.users au
                ON au.id = ct.user_id
              WHERE cc.organization_id = co.organization_id
                AND cc.company_id = co.id
            ) contacts ON TRUE
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(to_jsonb(a) ORDER BY a.is_primary DESC, a.created_at ASC) AS addresses
              FROM company_addresses a
              WHERE a.company_id = co.id
            ) addresses ON TRUE
            WHERE co.id = $1::uuid
              AND co.organization_id = $2::uuid
              AND co.status != $3
            """,
            company_id,
            organization_id,
            ClientStatus.DELETED.value,
        )
        if not row:
            return None
        result = dict(row)
        for key in ("contacts", "addresses"):
            val = result.get(key)
            if isinstance(val, str):
                try:
                    result[key] = json.loads(val)
                except Exception:
                    result[key] = []
        # primary_contact can be jsonb or already a dict
        if isinstance(result.get("primary_contact"), str):
            try:
                result["primary_contact"] = json.loads(result["primary_contact"])
            except Exception:
                result["primary_contact"] = None
        return result

    async def list_companies(
        self,
        *,
        organization_id: str,
        search: str | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List companies with pagination and optional search by name."""
        offset = (page - 1) * page_size
        args: list[Any] = [organization_id, ClientStatus.DELETED.value]
        where = ["organization_id = $1::uuid", "status != $2"]
        idx = 3
        if status:
            where.append(f"status = ${idx}")
            args.append(status)
            idx += 1
        if search:
            where.append(f"COALESCE(name,'') ILIKE ${idx}")
            args.append(f"%{search.strip()}%")
            idx += 1
        where_sql = " AND ".join(where)
        total = await self.db_connection.fetchval(
            f"SELECT COUNT(1) FROM companies WHERE {where_sql}",
            *args,
        )
        rows = await self.db_connection.fetch(
            f"""
            SELECT
              co.id::text AS id,
              co.organization_id::text AS organization_id,
              co.status,
              co.name,
              co.industry,
              co.profile_photo_url,
              co.primary_contact_id::text AS primary_contact_id,
              (ct.first_name || ' ' || ct.last_name) AS primary_contact_name,
              NULLIF(au.email::text, '') AS primary_contact_email,
              co.created_at,
              co.updated_at
            FROM companies co
            LEFT JOIN contacts ct
              ON ct.id = co.primary_contact_id
             AND ct.organization_id = co.organization_id
             AND ct.status != 'deleted'
            LEFT JOIN auth.users au
              ON au.id = ct.user_id
            WHERE {where_sql}
            ORDER BY co.created_at DESC
            OFFSET ${idx} LIMIT ${idx + 1}
            """,
            *(args + [offset, page_size]),
        )
        return [dict(r) for r in rows], int(total or 0)

