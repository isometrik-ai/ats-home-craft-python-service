"""Contacts persistence (public.contacts, public.contact_addresses) — asyncpg."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ClientStatus
from apps.user_service.app.utils.common_utils import parse_json_any
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

    @staticmethod
    def _coerce_jsonb_array_fields(
        row: dict[str, Any], field_names: tuple[str, ...]
    ) -> dict[str, Any]:
        """Coerce jsonb array-ish fields that may arrive as JSON strings."""
        out = dict(row)
        for json_field_name in field_names:
            raw_value = out.get(json_field_name)
            parsed = parse_json_any(raw_value, default=[])
            out[json_field_name] = parsed if isinstance(parsed, list) else []
        return out

    async def get_contact_id_by_email(self, *, organization_id: str, email: str) -> str | None:
        """Return an existing contact id for email within an organization (case-insensitive)."""
        email_norm = (email or "").strip().lower()
        if not email_norm:
            return None
        fetched_row = await self.db_connection.fetchrow(
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
        return str(fetched_row["id"]) if fetched_row and fetched_row.get("id") else None

    async def get_contact_ids_by_emails(
        self,
        *,
        organization_id: str,
        emails: list[str],
    ) -> dict[str, str]:
        """Return existing contact ids by normalized email within an org (bulk).

        Keys are normalized to lowercase/trimmed.
        """
        normed = [(e or "").strip().lower() for e in (emails or []) if (e or "").strip()]
        if not normed:
            return {}

        rows = await self.db_connection.fetch(
            """
            SELECT
              LOWER(COALESCE(au.email::text, '')) AS email_norm,
              ct.id::text AS id
            FROM contacts ct
            LEFT JOIN auth.users au
              ON au.id = ct.user_id
            WHERE ct.organization_id = $1::uuid
              AND ct.status != $2::text
              AND LOWER(COALESCE(au.email::text, '')) = ANY($3::text[])
            """,
            organization_id,
            ClientStatus.DELETED.value,
            normed,
        )
        out: dict[str, str] = {}
        for row in rows:
            email_norm = str(row["email_norm"] or "").strip().lower()
            cid = str(row["id"] or "")
            if email_norm and cid and email_norm not in out:
                out[email_norm] = cid
        return out

    async def create_contacts(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create contacts in bulk.

        This operation is intentionally idempotent for imports/retries:
        if a (organization_id, user_id) row already exists, we skip it.
        """
        if not rows:
            return []

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
            # The DB uses a *partial unique index* named uq_contacts_user_org, not a
            # UNIQUE CONSTRAINT.
            # Use index inference so Postgres matches the partial index predicate.
            on_conflict_sql=(
                "ON CONFLICT (organization_id, user_id) "
                "WHERE (user_id IS NOT NULL) AND (status <> 'deleted'::text) "
                "DO NOTHING"
            ),
        )

    async def get_contact_ids_by_user_ids(
        self,
        *,
        organization_id: str,
        user_ids: list[str],
    ) -> dict[str, str]:
        """Return contact ids by user_id within an org (bulk).

        Useful for import retries where inserts may be skipped due to unique constraints.
        """
        normed = [uid for uid in (user_ids or []) if str(uid or "").strip()]
        if not normed:
            return {}
        rows = await self.db_connection.fetch(
            """
            SELECT
              ct.user_id::text AS user_id,
              ct.id::text AS id
            FROM contacts ct
            WHERE ct.organization_id = $1::uuid
              AND ct.status != $2::text
              AND ct.user_id = ANY($3::uuid[])
            """,
            organization_id,
            ClientStatus.DELETED.value,
            normed,
        )
        out: dict[str, str] = {}
        for row in rows:
            uid = str(row.get("user_id") or "").strip()
            cid = str(row.get("id") or "").strip()
            if uid and cid and uid not in out:
                out[uid] = cid
        return out

    async def create_contact_with_optional_company_link(
        self,
        *,
        organization_id: str,
        contact_data: dict[str, Any],
        company_id: str | None,
        company_data: dict[str, Any] | None,
        company_addresses: list[dict[str, Any]] | None,
        make_primary: bool,
    ) -> dict[str, Any]:
        """Create contact and optionally link to a company in one DB round trip.

        - If `company_id` is provided: link to that company.
        - Else if `company_data` is provided: create company.
        - If `make_primary` is true and a company is selected: set contact as primary.

        Returns:
            dict with keys:
            - contact_id (str)
            - company_id (str | None)
            - contact (dict | None): inserted contact row
        """
        addresses_payload = company_addresses or []
        addresses_json = json.dumps(addresses_payload) if addresses_payload else None
        company_json = json.dumps([company_data]) if company_data else None

        fetched_row = await self.db_connection.fetchrow(
            """
            WITH company_exists AS (
              SELECT co.id
              FROM companies co
              WHERE $2::uuid IS NOT NULL
                AND co.id = $2::uuid
                AND co.organization_id = $1::uuid
                AND co.status != $20::text
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
                $3::uuid,
                $4::text,
                $5::text,
                $6::text,
                $7::text,
                $8::text,
                $9::text,
                $10::text,
                $11::date,
                $12::text,
                COALESCE($13::jsonb, '[]'::jsonb),
                COALESCE($14::text[], '{}'::text[]),
                COALESCE($15::jsonb, '{}'::jsonb),
                COALESCE($16::jsonb, '{}'::jsonb),
                COALESCE($17::jsonb, '{}'::jsonb)
              )
              RETURNING *
            ),
            new_company AS (
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
              SELECT
                $1::uuid,
                CASE WHEN $19::boolean IS TRUE THEN (SELECT id FROM contact) ELSE NULL END,
                c.status,
                c.name,
                c.industry,
                c.profile_photo_url,
                c.portal_access,
                c.email,
                c.phones,
                c.tags,
                c.websites,
                c.billing_preferences,
                c.social_pages,
                c.target_market_segments,
                c.current_tech_stack,
                c.preferred_communication_channels,
                c.industry_specific_terminologies,
                c.description,
                c.custom_fields,
                c.additional_data
              FROM jsonb_to_recordset(COALESCE($18::jsonb, '[]'::jsonb)) AS c(
                status text,
                name text,
                industry text,
                profile_photo_url text,
                portal_access boolean,
                email text,
                phones jsonb,
                tags text[],
                websites jsonb,
                billing_preferences jsonb,
                social_pages jsonb,
                target_market_segments text[],
                current_tech_stack text[],
                preferred_communication_channels text[],
                industry_specific_terminologies text[],
                description text,
                custom_fields jsonb,
                additional_data jsonb
              )
              WHERE $2::uuid IS NULL
              RETURNING *
            ),
            company AS (
              SELECT
                COALESCE(
                  (SELECT id FROM company_exists),
                  (SELECT id FROM new_company)
                ) AS id
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
              FROM jsonb_to_recordset(COALESCE($21::jsonb, '[]'::jsonb)) AS a(
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
              WHERE (SELECT id FROM company) IS NOT NULL
                AND $18::jsonb IS NOT NULL
              RETURNING 1
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
            company_json,
            bool(make_primary),
            ClientStatus.DELETED.value,
            addresses_json,
        )
        if not fetched_row:
            return {"contact_id": None, "company_id": None, "contact": None}
        contact_row = fetched_row.get("contact")
        return {
            "contact_id": str(fetched_row["contact_id"]),
            "company_id": fetched_row["company_id"],
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
        return {str(contact_row["id"]) for contact_row in rows}

    async def get_contact_for_update(self, *, contact_id: str, organization_id: str) -> dict | None:
        """Get a contact for update (DB-shaped details + `FOR UPDATE` lock).

        Returns the same shape as `get_contact_details` (companies/leads/addresses included),
        while locking the `contacts` row for consistency in update flows.
        """
        fetched_row = await self.db_connection.fetchrow(
            """
            SELECT
              ct.*,
              NULLIF(au.email::text, '') AS email,
              COALESCE(companies.companies, '[]'::jsonb) AS companies,
              COALESCE(leads.leads, '[]'::jsonb) AS leads,
              COALESCE(addresses.addresses, '[]'::jsonb) AS addresses
            FROM contacts ct
            LEFT JOIN auth.users au
              ON au.id = ct.user_id
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(
                jsonb_build_object(
                  'company_id', co.id::text,
                  'name',       co.name,
                  'industry',   co.industry,
                  'is_primary', COALESCE((co.primary_contact_id = ct.id), FALSE)
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
              SELECT jsonb_agg(
                jsonb_build_object(
                  'id',             l.id::text,
                  'name',           l.name,
                  'stage_id',       l.stage_id::text,
                  'stage_name',     ls.stage_name,
                  'deal_type',      l.deal_type,
                  'priority',       l.priority,
                  'lead_score',     l.lead_score,
                  'close_date',     l.close_date,
                  'amount',         l.amount,
                  'owner_id',       l.owner_id::text,
                  'lead_source',    l.lead_source,
                  'referral_source',l.referral_source,
                  'created_at',     l.created_at,
                  'updated_at',     l.updated_at
                )
                ORDER BY l.updated_at DESC NULLS LAST, l.created_at DESC
              ) FILTER (WHERE l.id IS NOT NULL) AS leads
              FROM lead_contacts lct
              INNER JOIN leads l
                ON l.id = lct.lead_id
               AND l.organization_id = lct.organization_id
              LEFT JOIN lead_stages ls
                ON ls.id = l.stage_id
               AND ls.organization_id = l.organization_id
              WHERE lct.organization_id = ct.organization_id
                AND lct.contact_id = ct.id
            ) leads ON TRUE
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(
                to_jsonb(addr) ORDER BY addr.is_primary DESC, addr.created_at ASC
              ) AS addresses
              FROM contact_addresses addr
              WHERE addr.contact_id = ct.id
            ) addresses ON TRUE
            WHERE ct.id = $1::uuid
              AND ct.organization_id = $2::uuid
              AND ct.status != $3
            FOR UPDATE OF ct
            """,
            contact_id,
            organization_id,
            ClientStatus.DELETED.value,
        )
        if not fetched_row:
            return None
        result = dict(fetched_row)
        return self._coerce_jsonb_array_fields(result, ("companies", "leads", "addresses"))

    async def get_contact_for_update_by_enrichment_request_id(
        self,
        *,
        enrichment_request_id: str,
    ) -> dict | None:
        """Load a contact row by enrichment_request_id with ``FOR UPDATE``.

        This is used by the enrichment webhook handler to apply updates idempotently.
        """
        if not enrichment_request_id:
            return None
        fetched_row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM contacts
            WHERE enrichment_request_id = $1
              AND status != $2
            FOR UPDATE
            """,
            enrichment_request_id,
            ClientStatus.DELETED.value,
        )
        return dict(fetched_row) if fetched_row else None

    async def update_contact(
        self,
        *,
        contact_id: str,
        organization_id: str,
        update_data: dict[str, Any],
    ) -> dict | None:
        """Update a contact."""
        id_param = len(update_data) + 1
        org_param = len(update_data) + 2
        status_param = len(update_data) + 3
        return await self.update_returning(
            table="contacts",
            where_sql=(
                f"WHERE id = ${id_param}::uuid "
                f"AND organization_id = ${org_param}::uuid "
                f"AND status != ${status_param}"
            ),
            where_params=[contact_id, organization_id, ClientStatus.DELETED.value],
            update_data=update_data,
            jsonb_columns=CONTACT_JSONB_COLUMNS,
            touch_updated_at=True,
        )

    async def soft_delete_contact(self, *, contact_id: str, organization_id: str) -> dict[str, Any]:
        """Soft delete a contact and return the updated row."""
        fetched_row = await self.db_connection.fetchrow(
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
        if not fetched_row:
            raise NotFoundException(
                message_key="clients.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return dict(fetched_row)

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
        next_id_param = len(update_data) + 1
        next_contact_param = len(update_data) + 2
        address_where = (
            f"WHERE id = ${next_id_param}::uuid AND contact_id = ${next_contact_param}::uuid"
        )
        return await self.update_returning(
            table="contact_addresses",
            where_sql=address_where,
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

    async def delete_all_contact_addresses(self, *, contact_id: str) -> None:
        """Delete all address rows for a contact."""
        await self.db_connection.execute(
            """
            DELETE FROM contact_addresses
            WHERE contact_id = $1::uuid
            """,
            contact_id,
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
        return [dict(address_row) for address_row in rows]

    async def get_contact_details(
        self,
        *,
        contact_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Get a contact + linked companies + addresses in one round trip."""
        fetched_row = await self.db_connection.fetchrow(
            """
            SELECT
              ct.*,
              NULLIF(au.email::text, '') AS email,
              COALESCE(companies.companies, '[]'::jsonb) AS companies,
              COALESCE(leads.leads, '[]'::jsonb) AS leads,
              COALESCE(addresses.addresses, '[]'::jsonb) AS addresses
            FROM contacts ct
            LEFT JOIN auth.users au
              ON au.id = ct.user_id
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(
                jsonb_build_object(
                  'company_id', co.id::text,
                  'name',       co.name,
                  'industry',   co.industry,
                  'is_primary', COALESCE((co.primary_contact_id = ct.id), FALSE)
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
              SELECT jsonb_agg(
                jsonb_build_object(
                  'id',             l.id::text,
                  'name',           l.name,
                  'stage_id',       l.stage_id::text,
                  'stage_name',     ls.stage_name,
                  'deal_type',      l.deal_type,
                  'priority',       l.priority,
                  'lead_score',     l.lead_score,
                  'close_date',     l.close_date,
                  'amount',         l.amount,
                  'owner_id',       l.owner_id::text,
                  'lead_source',    l.lead_source,
                  'referral_source',l.referral_source,
                  'created_at',     l.created_at,
                  'updated_at',     l.updated_at
                )
                ORDER BY l.updated_at DESC NULLS LAST, l.created_at DESC
              ) FILTER (WHERE l.id IS NOT NULL) AS leads
              FROM lead_contacts lct
              INNER JOIN leads l
                ON l.id = lct.lead_id
               AND l.organization_id = lct.organization_id
              LEFT JOIN lead_stages ls
                ON ls.id = l.stage_id
               AND ls.organization_id = l.organization_id
              WHERE lct.organization_id = ct.organization_id
                AND lct.contact_id = ct.id
            ) leads ON TRUE
            LEFT JOIN LATERAL (
              SELECT jsonb_agg(
                to_jsonb(addr) ORDER BY addr.is_primary DESC, addr.created_at ASC
              ) AS addresses
              FROM contact_addresses addr
              WHERE addr.contact_id = ct.id
            ) addresses ON TRUE
            WHERE ct.id = $1::uuid
              AND ct.organization_id = $2::uuid
              AND ct.status != $3
            """,
            contact_id,
            organization_id,
            ClientStatus.DELETED.value,
        )
        if not fetched_row:
            return None
        result = dict(fetched_row)
        return self._coerce_jsonb_array_fields(result, ("companies", "leads", "addresses"))

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
        next_param_index = 3
        if status:
            where.append(f"status = ${next_param_index}")
            args.append(status)
            next_param_index += 1
        if search:
            name_email_match = (
                f"(COALESCE(first_name,'') || ' ' || COALESCE(last_name,'') "
                f"ILIKE ${next_param_index} OR "
                f"COALESCE(au.email::text,'') ILIKE ${next_param_index})"
            )
            where.append(name_email_match)
            args.append(f"%{search.strip()}%")
            next_param_index += 1
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
                jsonb_agg(co.name ORDER BY co.name)
                  FILTER (WHERE co.id IS NOT NULL) AS company_names
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
            OFFSET ${next_param_index} LIMIT ${next_param_index + 1}
            """,
            *(args + [offset, page_size]),
        )
        contact_rows = [dict(contact_row) for contact_row in rows]
        return contact_rows, int(total or 0)
