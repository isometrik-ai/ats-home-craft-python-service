"""Contact-unit assignment persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import (
    ContactUnitRelationship,
    ContactUnitStatus,
)

_HOUSEHOLD_INVITATION_LATERAL_JOIN = """
LEFT JOIN LATERAL (
  SELECT
    hi.status,
    hi.token,
    hi.expires_at,
    hi.updated_at
  FROM household_invitations hi
  WHERE hi.contact_unit_id = cu.id
    AND hi.organization_id = cu.organization_id
  ORDER BY hi.updated_at DESC
  LIMIT 1
) hi ON true
"""

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
  c.last_name,
  cu.created_at
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

    # pylint: disable=too-many-public-methods

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

    async def owner_has_active_unit(
        self,
        *,
        organization_id: str,
        owner_contact_id: str,
        unit_id: str,
    ) -> bool:
        """True when an Owner contact has an active link to the unit."""
        row = await self.db_connection.fetchval(
            """
            SELECT 1
            FROM contact_units cu
            JOIN contacts c ON c.id = cu.contact_id
            WHERE cu.organization_id = $1::uuid
              AND cu.contact_id = $2::uuid
              AND cu.unit_id = $3::uuid
              AND cu.status = $4::contact_unit_status
              AND c.contact_type = 'Owner'
            LIMIT 1
            """,
            organization_id,
            owner_contact_id,
            unit_id,
            ContactUnitStatus.ACTIVE.value,
        )
        return row is not None

    async def insert_primary_occupant_link(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        contact_id: str,
    ) -> dict[str, Any]:
        """Link a tenant as the primary active occupant, clearing other primaries on the unit."""
        await self.db_connection.execute(
            """
            UPDATE contact_units
            SET is_primary = false,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND unit_id = $2::uuid
              AND status = $3::contact_unit_status
              AND is_primary = true
            """,
            organization_id,
            unit_id,
            ContactUnitStatus.ACTIVE.value,
        )
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO contact_units (
                organization_id, project_id, unit_id, contact_id,
                status, relationship, is_primary, activated_at
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid,
                $5::contact_unit_status, $6::contact_unit_relationship, true, now()
            )
            RETURNING id::text AS id
            """,
            organization_id,
            project_id,
            unit_id,
            contact_id,
            ContactUnitStatus.ACTIVE.value,
            ContactUnitRelationship.SELF.value,
        )
        return dict(row)

    async def get_by_unit_and_contact(
        self,
        *,
        organization_id: str,
        unit_id: str,
        contact_id: str,
    ) -> dict[str, Any] | None:
        """Fetch contact_unit link for a unit+contact regardless of status."""
        row = await self.db_connection.fetchrow(
            f"""
            {_CONTACT_UNIT_LIST_SQL}
              AND cu.unit_id = $2::uuid
              AND cu.contact_id = $3::uuid
            LIMIT 1
            """,
            organization_id,
            unit_id,
            contact_id,
        )
        return dict(row) if row else None

    async def sync_move_in(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
        event_date: Any,
    ) -> dict[str, Any] | None:
        """Activate a contact_unit link after a move-in event."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE contact_units
            SET status = $3::contact_unit_status,
                activated_at = COALESCE(activated_at, $4::timestamptz),
                moved_out_at = NULL,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            RETURNING id::text AS id, status::text AS status
            """,
            organization_id,
            contact_unit_id,
            ContactUnitStatus.ACTIVE.value,
            event_date,
        )
        return dict(row) if row else None

    async def sync_move_out(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
        event_date: Any,
    ) -> dict[str, Any] | None:
        """Mark a contact_unit link moved out after a move-out event."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE contact_units
            SET status = $3::contact_unit_status,
                moved_out_at = $4::timestamptz,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            RETURNING id::text AS id, status::text AS status
            """,
            organization_id,
            contact_unit_id,
            ContactUnitStatus.MOVED_OUT.value,
            event_date,
        )
        return dict(row) if row else None

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

    async def find_active_primary_conflicts(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_ids: list[str],
    ) -> list[str]:
        """Return unit_ids where activating pending primary rows would conflict."""
        if not contact_unit_ids:
            return []
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT cu_pending.unit_id::text AS unit_id
            FROM contact_units cu_pending
            INNER JOIN contact_units cu_existing
              ON cu_existing.unit_id = cu_pending.unit_id
             AND cu_existing.organization_id = cu_pending.organization_id
             AND cu_existing.is_primary = true
             AND cu_existing.status = 'active'::contact_unit_status
             AND cu_existing.id <> cu_pending.id
            WHERE cu_pending.organization_id = $1::uuid
              AND cu_pending.contact_id = $2::uuid
              AND cu_pending.id = ANY($3::uuid[])
              AND cu_pending.is_primary = true
              AND cu_pending.status = 'pending'::contact_unit_status
            """,
            organization_id,
            contact_id,
            contact_unit_ids,
        )
        return [str(row["unit_id"]) for row in rows]

    async def unit_has_primary_occupant(
        self,
        *,
        organization_id: str,
        unit_id: str,
        exclude_contact_id: str | None = None,
    ) -> bool:
        """Return True when the unit has a pending or active primary occupant."""
        args: list[Any] = [
            organization_id,
            unit_id,
            [ContactUnitStatus.PENDING.value, ContactUnitStatus.ACTIVE.value],
        ]
        exclude_filter = ""
        if exclude_contact_id:
            exclude_filter = " AND contact_id <> $4::uuid"
            args.append(exclude_contact_id)
        row = await self.db_connection.fetchrow(
            f"""
            SELECT 1
            FROM contact_units
            WHERE organization_id = $1::uuid
              AND unit_id = $2::uuid
              AND is_primary = true
              AND status = ANY($3::contact_unit_status[])
              {exclude_filter}
            LIMIT 1
            """,
            *args,
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

    async def activate_units_by_ids(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_ids: list[str],
    ) -> None:
        """Set activated_at on specific active contact_unit rows."""
        if not contact_unit_ids:
            return
        await self.db_connection.execute(
            """
            UPDATE contact_units
            SET activated_at = COALESCE(activated_at, now()),
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND id = ANY($3::uuid[])
              AND status = $4::contact_unit_status
            """,
            organization_id,
            contact_id,
            contact_unit_ids,
            ContactUnitStatus.ACTIVE.value,
        )

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

    async def activate_contact_unit(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any] | None:
        """Activate a pending contact_unit link after invitation acceptance."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE contact_units
            SET status = $3::contact_unit_status,
                activated_at = COALESCE(activated_at, now()),
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            RETURNING id::text AS id, status::text AS status
            """,
            organization_id,
            contact_unit_id,
            ContactUnitStatus.ACTIVE.value,
        )
        return dict(row) if row else None

    async def list_household_by_primary(
        self,
        *,
        organization_id: str,
        primary_contact_id: str,
        unit_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Family contacts linked to units the primary contact owns."""
        args: list[Any] = [
            organization_id,
            primary_contact_id,
            ContactUnitStatus.ACTIVE.value,
            [ContactUnitStatus.ACTIVE.value, ContactUnitStatus.PENDING.value],
        ]
        unit_filter = ""
        if unit_id:
            unit_filter = f" AND primary_cu.unit_id = ${len(args) + 1}::uuid"
            args.append(unit_id)
        rows = await self.db_connection.fetch(
            f"""
            SELECT
              cu.id::text AS contact_unit_id,
              cu.unit_id::text AS unit_id,
              cu.contact_id::text AS contact_id,
              cu.relationship::text AS relationship,
              cu.status::text AS unit_link_status,
              c.first_name,
              c.last_name,
              c.portal_access,
              c.phones,
              c.emails,
              c.user_id::text AS user_id,
              hi.status::text AS invitation_status,
              hi.token AS invitation_token,
              hi.expires_at AS invitation_expires_at,
              hi.updated_at AS invitation_sent_at
            FROM contact_units primary_cu
            JOIN contact_units cu
              ON cu.unit_id = primary_cu.unit_id
             AND cu.organization_id = primary_cu.organization_id
            JOIN contacts c ON c.id = cu.contact_id
            {_HOUSEHOLD_INVITATION_LATERAL_JOIN}
            WHERE primary_cu.organization_id = $1::uuid
              AND primary_cu.contact_id = $2::uuid
              AND primary_cu.status = $3::contact_unit_status
              AND cu.contact_id != $2::uuid
              AND c.contact_type = 'Family'
              AND cu.status = ANY($4::contact_unit_status[])
              {unit_filter}
            ORDER BY cu.created_at
            """,
            *args,
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

    async def get_household_member(
        self,
        *,
        organization_id: str,
        primary_contact_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one household member row visible to the primary contact."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
              cu.id::text AS contact_unit_id,
              cu.unit_id::text AS unit_id,
              cu.contact_id::text AS contact_id,
              cu.relationship::text AS relationship,
              cu.status::text AS unit_link_status,
              c.first_name,
              c.last_name,
              c.portal_access,
              c.phones,
              c.emails,
              c.user_id::text AS user_id,
              hi.status::text AS invitation_status,
              hi.token AS invitation_token,
              hi.expires_at AS invitation_expires_at,
              hi.updated_at AS invitation_sent_at
            FROM contact_units primary_cu
            JOIN contact_units cu
              ON cu.unit_id = primary_cu.unit_id
             AND cu.organization_id = primary_cu.organization_id
            JOIN contacts c ON c.id = cu.contact_id
            {_HOUSEHOLD_INVITATION_LATERAL_JOIN}
            WHERE primary_cu.organization_id = $1::uuid
              AND primary_cu.contact_id = $2::uuid
              AND primary_cu.status = $3::contact_unit_status
              AND cu.id = $4::uuid
              AND cu.contact_id != $2::uuid
              AND c.contact_type = 'Family'
              AND cu.status = ANY($5::contact_unit_status[])
            LIMIT 1
            """,
            organization_id,
            primary_contact_id,
            ContactUnitStatus.ACTIVE.value,
            contact_unit_id,
            [ContactUnitStatus.ACTIVE.value, ContactUnitStatus.PENDING.value],
        )
        return dict(row) if row else None

    async def update_household_relationship(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
        relationship: str,
    ) -> dict[str, Any] | None:
        """Update the relationship on a household contact_unit link."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE contact_units
            SET relationship = $3::contact_unit_relationship,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            RETURNING id::text AS id, relationship::text AS relationship
            """,
            organization_id,
            contact_unit_id,
            relationship,
        )
        return dict(row) if row else None

    async def update_household_link_status(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        """Update the status on a household contact_unit link."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE contact_units
            SET status = $3::contact_unit_status,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND id = $2::uuid
            RETURNING id::text AS id, status::text AS status
            """,
            organization_id,
            contact_unit_id,
            status,
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
