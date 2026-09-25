"""Persistence for facility staff assignments (which staff operate which facilities)."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_ASSIGNMENT_SELECT = """
    SELECT
        a.id::text AS id,
        a.project_member_id::text AS project_member_id,
        a.facility_ids::text[] AS facility_ids,
        a.created_at,
        a.updated_at,
        pm.user_id::text AS user_id,
        pm.status AS member_status,
        pr.slug AS role_slug,
        pr.name AS role_name,
        om.email,
        om.first_name,
        om.last_name
    FROM facility_staff_assignments a
    INNER JOIN project_members pm ON pm.id = a.project_member_id
    LEFT JOIN project_roles pr ON pr.id = pm.project_role_id
    LEFT JOIN organization_members om
      ON om.user_id = pm.user_id AND om.organization_id = pm.organization_id
"""


class FacilityStaffAssignmentsRepository(BaseRepository):
    """facility_staff_assignments."""

    async def list_assignments(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        rows = await self.db_connection.fetch(
            f"""
            {_ASSIGNMENT_SELECT}
            WHERE a.organization_id = $1::uuid AND a.project_id = $2::uuid
            ORDER BY om.first_name NULLS LAST, om.email
            """,
            organization_id,
            project_id,
        )
        return [dict(r) for r in rows]

    async def get_member(
        self, *, organization_id: str, project_id: str, project_member_id: str
    ) -> dict[str, Any] | None:
        row = await self.db_connection.fetchrow(
            """
            SELECT id::text AS id, user_id::text AS user_id, status
            FROM project_members
            WHERE id = $1::uuid AND organization_id = $2::uuid AND project_id = $3::uuid
            """,
            project_member_id,
            organization_id,
            project_id,
        )
        return dict(row) if row else None

    async def upsert_assignment(
        self,
        *,
        organization_id: str,
        project_id: str,
        project_member_id: str,
        facility_ids: list[str],
        user_id: str | None,
    ) -> str:
        value = await self.db_connection.fetchval(
            """
            INSERT INTO facility_staff_assignments
                (organization_id, project_id, project_member_id, facility_ids, created_by_user_id)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid[], $5::uuid)
            ON CONFLICT (project_member_id) DO UPDATE SET
                facility_ids = EXCLUDED.facility_ids,
                updated_at = NOW()
            RETURNING id::text
            """,
            organization_id,
            project_id,
            project_member_id,
            facility_ids,
            user_id,
        )
        return str(value)

    async def delete_assignment(
        self, *, organization_id: str, project_id: str, assignment_id: str
    ) -> bool:
        result = await self.db_connection.execute(
            """
            DELETE FROM facility_staff_assignments
            WHERE id = $1::uuid AND organization_id = $2::uuid AND project_id = $3::uuid
            """,
            assignment_id,
            organization_id,
            project_id,
        )
        return result.upper().endswith(" 1")

    async def facility_ids_for_user(
        self, *, organization_id: str, project_id: str, user_id: str
    ) -> list[str] | None:
        """Assigned facility ids for a staff user, or None when they have no assignment."""
        value = await self.db_connection.fetchval(
            """
            SELECT a.facility_ids::text[]
            FROM facility_staff_assignments a
            INNER JOIN project_members pm ON pm.id = a.project_member_id
            WHERE a.organization_id = $1::uuid
              AND a.project_id = $2::uuid
              AND pm.user_id = $3::uuid
            """,
            organization_id,
            project_id,
            user_id,
        )
        return list(value) if value is not None else None

    async def remove_facility(
        self, *, organization_id: str, project_id: str, facility_id: str
    ) -> None:
        await self.db_connection.execute(
            """
            UPDATE facility_staff_assignments
            SET facility_ids = array_remove(facility_ids, $3::uuid), updated_at = NOW()
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
              AND $3::uuid = ANY(facility_ids)
            """,
            organization_id,
            project_id,
            facility_id,
        )
