"""Persistence for public.contact_roles."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ContactRoleStatus, ContactType


class ContactRolesRepository(BaseRepository):
    """Database operations for contact role assignments."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with the request-scoped asyncpg connection."""
        super().__init__(db_connection=db_connection)

    async def insert_role(
        self,
        *,
        organization_id: str,
        contact_id: str,
        role_type: str,
        project_id: str | None = None,
        unit_id: str | None = None,
        relationship: str | None = None,
        contact_unit_id: str | None = None,
        status: str = ContactRoleStatus.ACTIVE.value,
    ) -> dict[str, Any]:
        """Insert a contact role row and return it."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO contact_roles (
                organization_id,
                contact_id,
                role_type,
                status,
                project_id,
                unit_id,
                relationship,
                contact_unit_id
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3::public.contact_role_type,
                $4::public.contact_role_status,
                $5::uuid,
                $6::uuid,
                $7::public.contact_unit_relationship,
                $8::uuid
            )
            RETURNING
                id::text AS id,
                organization_id::text AS organization_id,
                contact_id::text AS contact_id,
                role_type::text AS role_type,
                status::text AS status,
                project_id::text AS project_id,
                unit_id::text AS unit_id,
                relationship::text AS relationship,
                started_at,
                ended_at,
                contact_unit_id::text AS contact_unit_id,
                created_at,
                updated_at
            """,
            organization_id,
            contact_id,
            role_type,
            status,
            project_id,
            unit_id,
            relationship,
            contact_unit_id,
        )
        return dict(row) if row else {}

    async def end_active_roles_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
        role_types: list[str] | None = None,
        contact_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Mark active unit-scoped roles as ended."""
        args: list[Any] = [
            organization_id,
            unit_id,
            ContactRoleStatus.ENDED.value,
            ContactRoleStatus.ACTIVE.value,
        ]
        filters = [
            "organization_id = $1::uuid",
            "unit_id = $2::uuid",
            "status = $4::public.contact_role_status",
            "ended_at IS NULL",
        ]
        if role_types:
            args.append(role_types)
            filters.append(f"role_type = ANY(${len(args)}::public.contact_role_type[])")
        if contact_id:
            args.append(contact_id)
            filters.append(f"contact_id = ${len(args)}::uuid")

        rows = await self.db_connection.fetch(
            f"""
            UPDATE contact_roles
            SET status = $3::public.contact_role_status,
                ended_at = now(),
                updated_at = now()
            WHERE {" AND ".join(filters)}
            RETURNING
                id::text AS id,
                contact_id::text AS contact_id,
                role_type::text AS role_type,
                unit_id::text AS unit_id
            """,
            *args,
        )
        return [dict(row) for row in rows]

    async def list_active_roles_for_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> list[dict[str, Any]]:
        """List active roles for a contact."""
        rows = await self.db_connection.fetch(
            """
            SELECT
                id::text AS id,
                organization_id::text AS organization_id,
                contact_id::text AS contact_id,
                role_type::text AS role_type,
                status::text AS status,
                project_id::text AS project_id,
                unit_id::text AS unit_id,
                relationship::text AS relationship,
                started_at,
                ended_at,
                contact_unit_id::text AS contact_unit_id
            FROM contact_roles
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND status = $3::public.contact_role_status
              AND ended_at IS NULL
            ORDER BY started_at DESC
            """,
            organization_id,
            contact_id,
            ContactRoleStatus.ACTIVE.value,
        )
        return [dict(row) for row in rows]

    async def insert_owner_role(
        self,
        *,
        organization_id: str,
        contact_id: str,
        project_id: str,
        unit_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any]:
        """Insert an active Owner role for a unit allotment."""
        return await self.insert_role(
            organization_id=organization_id,
            contact_id=contact_id,
            role_type=ContactType.OWNER.value,
            project_id=project_id,
            unit_id=unit_id,
            contact_unit_id=contact_unit_id,
        )

    async def insert_tenant_role(
        self,
        *,
        organization_id: str,
        contact_id: str,
        project_id: str,
        unit_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any]:
        """Insert an active Tenant role for a unit occupancy."""
        return await self.insert_role(
            organization_id=organization_id,
            contact_id=contact_id,
            role_type=ContactType.TENANT.value,
            project_id=project_id,
            unit_id=unit_id,
            contact_unit_id=contact_unit_id,
        )

    async def insert_family_role(
        self,
        *,
        organization_id: str,
        contact_id: str,
        project_id: str,
        unit_id: str,
        contact_unit_id: str,
        relationship: str,
    ) -> dict[str, Any]:
        """Insert an active Family role for a household link."""
        return await self.insert_role(
            organization_id=organization_id,
            contact_id=contact_id,
            role_type=ContactType.FAMILY.value,
            project_id=project_id,
            unit_id=unit_id,
            relationship=relationship,
            contact_unit_id=contact_unit_id,
        )

    async def count_active_tenants_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> int:
        """Count active Tenant roles on a unit (0 or 1 by DB constraint)."""
        count = await self.db_connection.fetchval(
            """
            SELECT COUNT(*)::int
            FROM contact_roles
            WHERE organization_id = $1::uuid
              AND unit_id = $2::uuid
              AND role_type = $3::public.contact_role_type
              AND status = $4::public.contact_role_status
              AND ended_at IS NULL
            """,
            organization_id,
            unit_id,
            ContactType.TENANT.value,
            ContactRoleStatus.ACTIVE.value,
        )
        return int(count or 0)

    async def get_active_tenant_contact_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> str | None:
        """Return a contact id when an active Tenant role exists on the unit."""
        row = await self.db_connection.fetchrow(
            """
            SELECT contact_id::text AS contact_id
            FROM contact_roles
            WHERE organization_id = $1::uuid
              AND unit_id = $2::uuid
              AND role_type = $3::public.contact_role_type
              AND status = $4::public.contact_role_status
              AND ended_at IS NULL
            LIMIT 1
            """,
            organization_id,
            unit_id,
            ContactType.TENANT.value,
            ContactRoleStatus.ACTIVE.value,
        )
        return str(row["contact_id"]) if row else None
