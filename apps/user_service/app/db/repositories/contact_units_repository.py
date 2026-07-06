"""Contact-unit assignment persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ContactUnitStatus

_CONTACT_UNIT_LIST_SQL = """
SELECT
  cu.id::text AS id,
  cu.unit_id::text AS unit_id,
  cu.project_id::text AS project_id,
  cu.contact_id::text AS contact_id,
  cu.status::text AS status,
  cu.is_primary,
  cu.is_default_login,
  cu.relationship::text AS relationship,
  u.code,
  u.unit_label,
  t.name AS tower_name,
  f.display_name AS floor_name,
  uc.display_label AS config_label,
  c.contact_type,
  c.first_name,
  c.last_name
FROM contact_units cu
JOIN units u ON u.id = cu.unit_id
JOIN contacts c ON c.id = cu.contact_id
LEFT JOIN towers t ON t.id = u.tower_id
LEFT JOIN floors f ON f.id = u.floor_id
LEFT JOIN unit_configs uc ON uc.id = u.config_id
WHERE cu.organization_id = $1::uuid
"""


class ContactUnitsRepository(BaseRepository):
    """Database operations for public.contact_units."""

    async def list_by_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List contact units for a contact with display joins."""
        args: list[Any] = [organization_id, contact_id]
        where = ["cu.contact_id = $2::uuid"]
        if statuses:
            where.append(f"cu.status = ANY(${len(args) + 1}::contact_unit_status[])")
            args.append(statuses)
        where_sql = " AND ".join(where)
        rows = await self.db_connection.fetch(
            f"""
            {_CONTACT_UNIT_LIST_SQL}
              AND {where_sql}
            ORDER BY cu.sort_order, cu.created_at
            """,
            *args,
        )
        return [dict(row) for row in rows]

    async def get_by_id(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one contact_unit with display joins."""
        row = await self.db_connection.fetchrow(
            f"""
            {_CONTACT_UNIT_LIST_SQL}
              AND cu.id = $2::uuid
            LIMIT 1
            """,
            organization_id,
            contact_unit_id,
        )
        return dict(row) if row else None

    async def get_owned_by_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any] | None:
        """Fetch contact_unit if it belongs to the contact."""
        row = await self.db_connection.fetchrow(
            f"""
            {_CONTACT_UNIT_LIST_SQL}
              AND cu.id = $2::uuid
              AND cu.contact_id = $3::uuid
            LIMIT 1
            """,
            organization_id,
            contact_unit_id,
            contact_id,
        )
        return dict(row) if row else None

    async def contact_has_active_unit(
        self,
        *,
        organization_id: str,
        contact_id: str,
        unit_id: str,
    ) -> bool:
        """True if contact has an active link to the unit."""
        row = await self.db_connection.fetchval(
            """
            SELECT 1
            FROM contact_units
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND unit_id = $3::uuid
              AND status = $4::contact_unit_status
            LIMIT 1
            """,
            organization_id,
            contact_id,
            unit_id,
            ContactUnitStatus.ACTIVE.value,
        )
        return row is not None

    async def count_active_units(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> int:
        """Count active units for a contact."""
        count = await self.db_connection.fetchval(
            """
            SELECT COUNT(*)
            FROM contact_units
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND status = $3::contact_unit_status
            """,
            organization_id,
            contact_id,
            ContactUnitStatus.ACTIVE.value,
        )
        return int(count or 0)

    async def has_default_login(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> bool:
        """True if contact has a default login unit among active links."""
        row = await self.db_connection.fetchval(
            """
            SELECT 1
            FROM contact_units
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND status = $3::contact_unit_status
              AND is_default_login = true
            LIMIT 1
            """,
            organization_id,
            contact_id,
            ContactUnitStatus.ACTIVE.value,
        )
        return row is not None

    async def confirm_selection(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Activate pending contact_units selected by the contact."""
        rows = await self.db_connection.fetch(
            """
            UPDATE contact_units cu
            SET status = $4::contact_unit_status,
                claimed_at = COALESCE(claimed_at, now()),
                updated_at = now()
            WHERE cu.organization_id = $1::uuid
              AND cu.contact_id = $2::uuid
              AND cu.id = ANY($3::uuid[])
              AND cu.status = $5::contact_unit_status
            RETURNING cu.id::text AS id, cu.status::text AS status
            """,
            organization_id,
            contact_id,
            contact_unit_ids,
            ContactUnitStatus.ACTIVE.value,
            ContactUnitStatus.PENDING.value,
        )
        return [dict(row) for row in rows]

    async def set_default_login(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any] | None:
        """Set one active contact_unit as default login."""
        async with self.db_connection.transaction():
            await self.db_connection.execute(
                """
                UPDATE contact_units
                SET is_default_login = false,
                    updated_at = now()
                WHERE organization_id = $1::uuid
                  AND contact_id = $2::uuid
                  AND status = $3::contact_unit_status
                """,
                organization_id,
                contact_id,
                ContactUnitStatus.ACTIVE.value,
            )
            row = await self.db_connection.fetchrow(
                """
                UPDATE contact_units
                SET is_default_login = true,
                    updated_at = now()
                WHERE organization_id = $1::uuid
                  AND contact_id = $2::uuid
                  AND id = $3::uuid
                  AND status = $4::contact_unit_status
                RETURNING id::text AS id, is_default_login
                """,
                organization_id,
                contact_id,
                contact_unit_id,
                ContactUnitStatus.ACTIVE.value,
            )
        return dict(row) if row else None

    async def activate_for_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> None:
        """Set activated_at on active contact units."""
        await self.db_connection.execute(
            """
            UPDATE contact_units
            SET activated_at = COALESCE(activated_at, now()),
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND status = $3::contact_unit_status
            """,
            organization_id,
            contact_id,
            ContactUnitStatus.ACTIVE.value,
        )

    async def insert_allotment(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        contact_id: str,
        is_primary: bool = False,
        relationship: str = "self",
        status: str = ContactUnitStatus.PENDING.value,
    ) -> dict[str, Any]:
        """Insert a contact-unit allotment row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO contact_units (
                organization_id, project_id, unit_id, contact_id,
                is_primary, status, relationship
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5,
                $6::contact_unit_status, $7::contact_unit_relationship
            )
            RETURNING id::text AS id, status::text AS status
            """,
            organization_id,
            project_id,
            unit_id,
            contact_id,
            is_primary,
            status,
            relationship,
        )
        return dict(row)

    async def insert_household_link(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        contact_id: str,
        relationship: str,
        status: str = ContactUnitStatus.ACTIVE.value,
    ) -> dict[str, Any]:
        """Link a family contact to a unit."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO contact_units (
                organization_id, project_id, unit_id, contact_id,
                status, relationship, is_primary
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid,
                $5::contact_unit_status, $6::contact_unit_relationship, false
            )
            RETURNING id::text AS id
            """,
            organization_id,
            project_id,
            unit_id,
            contact_id,
            status,
            relationship,
        )
        return dict(row)

    async def list_household_by_primary(
        self,
        *,
        organization_id: str,
        primary_contact_id: str,
    ) -> list[dict[str, Any]]:
        """Family contacts linked to units the primary contact owns."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              cu.id::text AS contact_unit_id,
              cu.unit_id::text AS unit_id,
              cu.contact_id::text AS contact_id,
              cu.relationship::text AS relationship,
              c.first_name,
              c.last_name,
              c.portal_access,
              c.phones
            FROM contact_units primary_cu
            JOIN contact_units cu
              ON cu.unit_id = primary_cu.unit_id
             AND cu.organization_id = primary_cu.organization_id
            JOIN contacts c ON c.id = cu.contact_id
            WHERE primary_cu.organization_id = $1::uuid
              AND primary_cu.contact_id = $2::uuid
              AND primary_cu.status = $3::contact_unit_status
              AND cu.contact_id != $2::uuid
              AND c.contact_type = 'Family'
              AND cu.status = $3::contact_unit_status
            ORDER BY cu.created_at
            """,
            organization_id,
            primary_contact_id,
            ContactUnitStatus.ACTIVE.value,
        )
        return [dict(row) for row in rows]

    async def get_household_link(
        self,
        *,
        organization_id: str,
        primary_contact_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a household link if it belongs to a unit the primary owns.

        Ensures the target link is a Family contact (not the primary's own link)
        on a unit the primary contact is actively linked to.
        """
        row = await self.db_connection.fetchrow(
            """
            SELECT
              cu.id::text AS contact_unit_id,
              cu.contact_id::text AS contact_id,
              cu.unit_id::text AS unit_id
            FROM contact_units cu
            JOIN contacts c ON c.id = cu.contact_id
            JOIN contact_units primary_cu
              ON primary_cu.unit_id = cu.unit_id
             AND primary_cu.organization_id = cu.organization_id
            WHERE cu.organization_id = $1::uuid
              AND cu.id = $2::uuid
              AND cu.contact_id != $3::uuid
              AND c.contact_type = 'Family'
              AND primary_cu.contact_id = $3::uuid
              AND primary_cu.status = $4::contact_unit_status
            LIMIT 1
            """,
            organization_id,
            contact_unit_id,
            primary_contact_id,
            ContactUnitStatus.ACTIVE.value,
        )
        return dict(row) if row else None

    async def delete_link(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
    ) -> bool:
        """Delete a contact_unit link. Returns True if a row was removed."""
        result = await self.db_connection.execute(
            """
            DELETE FROM contact_units
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            """,
            organization_id,
            contact_unit_id,
        )
        return result.upper().startswith("DELETE") and not result.endswith(" 0")

    async def count_links_for_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> int:
        """Count remaining contact_unit links for a contact."""
        count = await self.db_connection.fetchval(
            """
            SELECT COUNT(*)
            FROM contact_units
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
            """,
            organization_id,
            contact_id,
        )
        return int(count or 0)

    async def get_unit_project(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> dict[str, Any] | None:
        """Load unit org/project for allotment validation."""
        row = await self.db_connection.fetchrow(
            """
            SELECT id::text AS id, organization_id::text AS organization_id,
                   project_id::text AS project_id
            FROM units
            WHERE id = $1::uuid
              AND organization_id = $2::uuid
            LIMIT 1
            """,
            unit_id,
            organization_id,
        )
        return dict(row) if row else None
