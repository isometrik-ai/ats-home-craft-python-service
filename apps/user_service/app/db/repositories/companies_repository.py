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

    async def create_companies(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        required = ["organization_id", "name"]
        optional = [
            "primary_contact_id",
            "status",
            "industry",
            "profile_photo_url",
            "portal_access",
            "tags",
            "websites",
            "billing_preferences",
            "custom_fields",
            "additional_data",
            "social_pages",
            "linked_pages",
            "products",
            "key_people",
            "sales_intelligence",
            "description",
            "target_market_segments",
            "current_tech_stack",
            "preferred_communication_channels",
            "industry_specific_terminologies",
            "enrichment_done",
            "enrichment_status",
            "enrichment_request_id",
            "last_enriched_at",
        ]
        return await self.bulk_insert_returning(
            table="companies",
            required_columns=required,
            optional_columns=optional,
            rows=rows,
            jsonb_columns=COMPANY_JSONB_COLUMNS,
        )

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

    async def clear_primary_contact_if_matches(
        self,
        *,
        company_id: str,
        organization_id: str,
        contact_id: str,
    ) -> bool:
        """Clear primary contact only when it currently matches `contact_id`.

        Returns:
            bool: True when an update occurred, False otherwise.
        """
        row = await self.db_connection.fetchrow(
            """
            UPDATE companies
            SET primary_contact_id = NULL, updated_at = NOW()
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
              AND primary_contact_id = $3::uuid
            RETURNING id
            """,
            company_id,
            organization_id,
            contact_id,
        )
        return bool(row)

    async def soft_delete_company(self, *, company_id: str, organization_id: str) -> None:
        row = await self.db_connection.fetchrow(
            """
            UPDATE companies
            SET status = $3, updated_at = NOW()
            WHERE id = $1::uuid AND organization_id = $2::uuid AND status != $3
            RETURNING id
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

    async def get_company_addresses(self, *, company_id: str) -> list[dict[str, Any]]:
        rows = await self.db_connection.fetch(
            """
            SELECT *
            FROM company_addresses
            WHERE company_id = $1::uuid
            ORDER BY is_primary DESC, created_at ASC
            """,
            company_id,
        )
        return [dict(r) for r in rows]

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
                'email',      ct.email,
                'phones',     ct.phones
              ) AS primary_contact
              FROM contacts ct
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
                  'email',      ct.email,
                  'is_primary', (co.primary_contact_id = ct.id)
                )
                ORDER BY ct.created_at ASC
              ) FILTER (WHERE ct.id IS NOT NULL) AS contacts
              FROM contact_companies cc
              INNER JOIN contacts ct
                ON ct.id = cc.contact_id
               AND ct.organization_id = co.organization_id
               AND ct.status != 'deleted'
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
              ct.email AS primary_contact_email,
              co.created_at,
              co.updated_at
            FROM companies co
            LEFT JOIN contacts ct
              ON ct.id = co.primary_contact_id
             AND ct.organization_id = co.organization_id
             AND ct.status != 'deleted'
            WHERE {where_sql}
            ORDER BY co.created_at DESC
            OFFSET ${idx} LIMIT ${idx + 1}
            """,
            *(args + [offset, page_size]),
        )
        return [dict(r) for r in rows], int(total or 0)

