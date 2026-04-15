"""Companies persistence (public.companies, public.company_addresses) — asyncpg."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ClientStatus

COMPANY_JSONB_COLUMNS: frozenset[str] = frozenset(
    {
        "phones",
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
    """Persistence for ``companies`` and ``company_addresses`` rows."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with the request-scoped asyncpg connection."""
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
        """Create company plus optional addresses and optional contact in one round trip.

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
        contact_addresses_json = (
            json.dumps(contact_addresses_payload) if contact_addresses_payload else None
        )

        if contact_id is not None and contact_data is not None:
            raise ValueError("Provide only one of contact_id or contact_data.")

        fetched_row = await self.db_connection.fetchrow(
            """
            WITH contact_exists AS (
              SELECT ct.id
              FROM contacts ct
              WHERE $21::uuid IS NOT NULL
                AND ct.id = $21::uuid
                AND ct.organization_id = $1::uuid
                AND ct.status != $22::text
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
              FROM jsonb_to_recordset(COALESCE($23::jsonb, '[]'::jsonb)) AS c(
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
              WHERE $21::uuid IS NULL
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
                email,
                phones,
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
                CASE WHEN $25::boolean IS TRUE THEN (SELECT id FROM contact) ELSE NULL END,
                $2::text,
                $3::text,
                $4::text,
                $5::text,
                $6::boolean,
                $7::text,
                COALESCE($8::jsonb, '[]'::jsonb),
                $9::text[],
                COALESCE($10::jsonb, '[]'::jsonb),
                COALESCE($11::jsonb, '{}'::jsonb),
                COALESCE($12::jsonb, '[]'::jsonb),
                $13::text[],
                $14::text[],
                $15::text[],
                $16::text[],
                $17::text,
                COALESCE($18::jsonb, '[]'::jsonb),
                COALESCE($19::jsonb, '{}'::jsonb)
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
              FROM jsonb_to_recordset(COALESCE($20::jsonb, '[]'::jsonb)) AS a(
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
              FROM jsonb_to_recordset(COALESCE($24::jsonb, '[]'::jsonb)) AS a(
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
              (
                SELECT to_jsonb(co)
                FROM companies AS co
                WHERE co.id = (SELECT id FROM company)
              ) AS company,
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
            company_data.get("email"),
            company_data.get("phones"),
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
        if not fetched_row:
            return {
                "company_id": None,
                "company": None,
                "contact_id": None,
                "contact": None,
                "contact_found": False,
            }
        company_row = fetched_row.get("company")
        contact_row = fetched_row.get("contact")
        return {
            "company_id": str(fetched_row["company_id"]),
            "company": dict(company_row) if isinstance(company_row, dict) else company_row,
            "contact_id": fetched_row.get("contact_id"),
            "contact": dict(contact_row) if isinstance(contact_row, dict) else contact_row,
            "contact_found": bool(fetched_row.get("contact_found")),
        }

    async def get_company_for_update(self, *, company_id: str, organization_id: str) -> dict | None:
        """Load a company row with ``FOR UPDATE`` or return None when missing."""
        fetched_row = await self.db_connection.fetchrow(
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
        return dict(fetched_row) if fetched_row else None

    async def get_company_for_update_by_enrichment_request_id(
        self,
        *,
        enrichment_request_id: str,
    ) -> dict | None:
        """Load a company row by enrichment_request_id with ``FOR UPDATE``.

        Used by the enrichment webhook handler to apply updates idempotently.
        """
        if not enrichment_request_id:
            return None
        fetched_row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM companies
            WHERE enrichment_request_id = $1
              AND status != $2
            FOR UPDATE
            """,
            enrichment_request_id,
            ClientStatus.DELETED.value,
        )
        return dict(fetched_row) if fetched_row else None

    async def update_company(
        self,
        *,
        company_id: str,
        organization_id: str,
        update_data: dict[str, Any],
    ) -> dict | None:
        """Update scalar and JSONB columns on a company and return the updated row."""
        id_param = len(update_data) + 1
        org_param = len(update_data) + 2
        status_param = len(update_data) + 3
        return await self.update_returning(
            table="companies",
            where_sql=(
                f"WHERE id = ${id_param}::uuid "
                f"AND organization_id = ${org_param}::uuid "
                f"AND status != ${status_param}"
            ),
            where_params=[company_id, organization_id, ClientStatus.DELETED.value],
            update_data=update_data,
            jsonb_columns=COMPANY_JSONB_COLUMNS,
            touch_updated_at=True,
        )

    async def create_company_addresses(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert one or more ``company_addresses`` rows and return inserted rows."""
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
        id_param = len(update_data) + 1
        company_param = len(update_data) + 2
        return await self.update_returning(
            table="company_addresses",
            where_sql=f"WHERE id = ${id_param}::uuid AND company_id = ${company_param}::uuid",
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
        """Get a company + member contacts (same shape as list) + addresses in one round trip."""
        fetched_row = await self.db_connection.fetchrow(
            """
            SELECT
              co.*,
              COALESCE(contacts.contacts, '[]'::jsonb) AS contacts,
              COALESCE(addresses.addresses, '[]'::jsonb) AS addresses
            FROM companies co
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(
                jsonb_build_object(
                  'id',         ct.id::text,
                  'first_name', ct.first_name,
                  'last_name',  ct.last_name,
                  'title',      ct.title,
                  'email',      NULLIF(au.email::text, ''),
                  'phones',     COALESCE(ct.phones, '[]'::jsonb),
                  'is_primary', (co.primary_contact_id IS NOT NULL
                                 AND co.primary_contact_id = ct.id)
                )
                ORDER BY (co.primary_contact_id IS NOT NULL
                          AND co.primary_contact_id = ct.id) DESC,
                         ct.created_at ASC
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
              SELECT jsonb_agg(
                to_jsonb(addr) ORDER BY addr.is_primary DESC, addr.created_at ASC
              ) AS addresses
              FROM company_addresses addr
              WHERE addr.company_id = co.id
            ) addresses ON TRUE
            WHERE co.id = $1::uuid
              AND co.organization_id = $2::uuid
              AND co.status != $3
            """,
            company_id,
            organization_id,
            ClientStatus.DELETED.value,
        )
        if not fetched_row:
            return None
        result = dict(fetched_row)
        for json_field_name in ("contacts", "addresses"):
            raw_json_value = result.get(json_field_name)
            if isinstance(raw_json_value, str):
                try:
                    result[json_field_name] = json.loads(raw_json_value)
                except json.JSONDecodeError:
                    result[json_field_name] = []
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
        where = ["co.organization_id = $1::uuid", "co.status != $2"]
        next_param_index = 3
        if status:
            where.append(f"co.status = ${next_param_index}")
            args.append(status)
            next_param_index += 1
        if search:
            where.append(f"COALESCE(co.name,'') ILIKE ${next_param_index}")
            args.append(f"%{search.strip()}%")
            next_param_index += 1
        where_sql = " AND ".join(where)
        total = await self.db_connection.fetchval(
            f"SELECT COUNT(1) FROM companies co WHERE {where_sql}",
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
              co.email,
              COALESCE(co.phones, '[]'::jsonb) AS phones,
              COALESCE(member_contacts.contacts, '[]'::jsonb) AS contacts,
              co.created_at,
              co.updated_at
            FROM companies co
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(
                jsonb_build_object(
                  'id',         ct.id::text,
                  'first_name', ct.first_name,
                  'last_name',  ct.last_name,
                  'title',      ct.title,
                  'email',      NULLIF(au.email::text, ''),
                  'phones',     COALESCE(ct.phones, '[]'::jsonb),
                  'is_primary', (co.primary_contact_id IS NOT NULL
                                 AND co.primary_contact_id = ct.id)
                )
                ORDER BY (co.primary_contact_id IS NOT NULL
                          AND co.primary_contact_id = ct.id) DESC,
                         ct.created_at ASC
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
            ) member_contacts ON TRUE
            WHERE {where_sql}
            ORDER BY co.created_at DESC
            OFFSET ${next_param_index} LIMIT ${next_param_index + 1}
            """,
            *(args + [offset, page_size]),
        )
        company_rows = [dict(company_row) for company_row in rows]
        return company_rows, int(total or 0)
