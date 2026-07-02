"""Contacts persistence aligned with public.contacts (Supabase migration)."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ContactStatus
from apps.user_service.app.utils.common_utils import parse_json_any
from libs.shared_utils.custom_field_filtering import build_dropdown_jsonb_where

CONTACT_JSONB_COLUMNS: frozenset[str] = frozenset(
    {
        "phones",
        "emails",
        "custom_fields",
        "additional_data",
        "social_pages",
        "documents",
        "websites",
        "notes",
        "communication_preferences",
    }
)


class ContactsRepository(BaseRepository):
    """Database operations for public.contacts."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        super().__init__(db_connection=db_connection)

    @staticmethod
    def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        """Coerce jsonb fields that may arrive as JSON strings."""
        out = dict(row)
        for field in CONTACT_JSONB_COLUMNS:
            parsed = parse_json_any(out.get(field), default=out.get(field))
            out[field] = parsed
        if out.get("tags") is None:
            out["tags"] = []
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
            ContactStatus.DELETED.value,
            email_norm,
        )
        return str(fetched_row["id"]) if fetched_row and fetched_row.get("id") else None

    async def is_active_contact_user_for_organization(
        self,
        *,
        user_id: str,
        organization_id: str,
    ) -> bool:
        """Return True if the auth user is an active contact in the organization."""
        if not user_id or not organization_id:
            return False
        row = await self.db_connection.fetchval(
            """
            SELECT 1
            FROM contacts ct
            WHERE ct.user_id = $1::uuid
              AND ct.organization_id = $2::uuid
              AND ct.status != $3::text
            LIMIT 1
            """,
            user_id,
            organization_id,
            ContactStatus.DELETED.value,
        )
        return row is not None

    async def get_active_contact_by_user_id(
        self,
        *,
        user_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Return active contact for auth user within organization."""
        if not user_id or not organization_id:
            return None
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM contacts
            WHERE user_id = $1::uuid
              AND organization_id = $2::uuid
              AND status != $3
            LIMIT 1
            """,
            user_id,
            organization_id,
            ContactStatus.DELETED.value,
        )
        return self._normalize_row(dict(row)) if row else None

    async def insert_contact(self, contact_data: dict[str, Any]) -> dict[str, Any]:
        """Insert one contact row and return it."""
        columns = [
            "id",
            "organization_id",
            "user_id",
            "isometrik_user_id",
            "status",
            "contact_type",
            "portal_access",
            "prefix",
            "first_name",
            "middle_name",
            "last_name",
            "title",
            "date_of_birth",
            "profile_photo_url",
            "gender",
            "blood_group",
            "communication_preferences",
            "phones",
            "emails",
            "tags",
            "custom_fields",
            "additional_data",
            "social_pages",
            "documents",
            "description",
            "websites",
            "notes",
        ]
        present = [col for col in columns if col in contact_data]
        if "organization_id" not in present:
            raise ValueError("organization_id is required")
        if "contact_type" not in present:
            raise ValueError("contact_type is required")

        col_sql = ", ".join(present)
        placeholders = ", ".join(f"${idx + 1}" for idx in range(len(present)))

        from apps.user_service.app.utils.common_utils import serialize_jsonb_param

        values = [
            serialize_jsonb_param(col, contact_data.get(col), CONTACT_JSONB_COLUMNS)
            for col in present
        ]

        row = await self.db_connection.fetchrow(
            f"""
            INSERT INTO contacts ({col_sql})
            VALUES ({placeholders})
            RETURNING *
            """,
            *values,
        )
        return self._normalize_row(dict(row))

    async def get_contact_for_update(self, *, contact_id: str, organization_id: str) -> dict | None:
        """Fetch contact row for update/delete guards."""
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM contacts
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
              AND status != $3
            """,
            contact_id,
            organization_id,
            ContactStatus.DELETED.value,
        )
        return self._normalize_row(dict(row)) if row else None

    async def get_contact_details(
        self,
        *,
        contact_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a single contact scoped to the organization."""
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM contacts
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
              AND status != $3
            """,
            contact_id,
            organization_id,
            ContactStatus.DELETED.value,
        )
        return self._normalize_row(dict(row)) if row else None

    async def update_contact(
        self,
        *,
        contact_id: str,
        organization_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch contact columns that are present in update_data."""
        if not update_data:
            row = await self.get_contact_details(
                contact_id=contact_id, organization_id=organization_id
            )
            return row

        from apps.user_service.app.utils.common_utils import serialize_jsonb_param

        set_parts: list[str] = []
        values: list[Any] = []
        idx = 1
        for col, val in update_data.items():
            if col in {"id", "organization_id", "created_at"}:
                continue
            set_parts.append(
                f"{col} = ${idx}" if col not in CONTACT_JSONB_COLUMNS else f"{col} = ${idx}::jsonb"
            )
            values.append(serialize_jsonb_param(col, val, CONTACT_JSONB_COLUMNS))
            idx += 1

        set_parts.append("updated_at = now()")
        values.extend([contact_id, organization_id, ContactStatus.DELETED.value])

        row = await self.db_connection.fetchrow(
            f"""
            UPDATE contacts
            SET {", ".join(set_parts)}
            WHERE id = ${idx}::uuid
              AND organization_id = ${idx + 1}::uuid
              AND status != ${idx + 2}
            RETURNING *
            """,
            *values,
        )
        return self._normalize_row(dict(row)) if row else None

    async def soft_delete_contact(
        self,
        *,
        contact_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Soft-delete contact and clear user_id per DB constraint."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE contacts
            SET status = $4,
                user_id = NULL,
                updated_at = now()
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
              AND status != $3
            RETURNING *
            """,
            contact_id,
            organization_id,
            ContactStatus.DELETED.value,
            ContactStatus.DELETED.value,
        )
        return self._normalize_row(dict(row)) if row else None

    async def list_contacts(
        self,
        *,
        organization_id: str,
        search: str | None,
        status: str | None,
        contact_type: str | None,
        dropdown_filters: dict[str, list[str]] | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List contacts with search and pagination."""
        offset = (page - 1) * page_size
        args: list[Any] = [organization_id, ContactStatus.DELETED.value]
        where = ["ct.organization_id = $1::uuid", "ct.status != $2"]
        next_param_index = 3

        if status:
            where.append(f"ct.status = ${next_param_index}")
            args.append(status)
            next_param_index += 1

        if contact_type:
            where.append(f"ct.contact_type = ${next_param_index}")
            args.append(contact_type)
            next_param_index += 1

        if search:
            where.append(
                f"""(
                  COALESCE(ct.first_name, '') || ' ' || COALESCE(ct.last_name, '')
                    ILIKE ${next_param_index}
                  OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(COALESCE(ct.emails, '[]'::jsonb)) AS e(item)
                    WHERE COALESCE(e.item->>'email', '') ILIKE ${next_param_index}
                  )
                )"""
            )
            args.append(f"%{search.strip()}%")
            next_param_index += 1

        if dropdown_filters:
            dropdown_where, dropdown_args, next_param_index = build_dropdown_jsonb_where(
                custom_fields_column_sql="ct.custom_fields",
                filters=dropdown_filters,
                param_start_index=next_param_index,
            )
            if dropdown_where:
                where.append(dropdown_where)
                args.extend(dropdown_args)

        where_sql = " AND ".join(where)
        total = await self.db_connection.fetchval(
            f"SELECT COUNT(1) FROM contacts ct WHERE {where_sql}",
            *args,
        )

        rows = await self.db_connection.fetch(
            f"""
            SELECT
              ct.id::text AS id,
              ct.organization_id::text AS organization_id,
              ct.status,
              ct.contact_type,
              ct.portal_access,
              ct.first_name,
              ct.last_name,
              ct.title,
              ct.emails,
              ct.profile_photo_url,
              ct.phones,
              ct.tags,
              ct.created_at,
              ct.updated_at
            FROM contacts ct
            WHERE {where_sql}
            ORDER BY ct.created_at DESC
            OFFSET ${next_param_index} LIMIT ${next_param_index + 1}
            """,
            *(args + [offset, page_size]),
        )
        return [self._normalize_row(dict(row)) for row in rows], int(total or 0)
