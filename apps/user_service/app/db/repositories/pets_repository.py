"""Pet persistence for household pets (ADR 0016)."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums.pets import PetStatus

_PET_SELECT_COLUMNS = """
              p.id::text AS id,
              p.organization_id::text AS organization_id,
              p.project_id::text AS project_id,
              p.unit_id::text AS unit_id,
              p.created_by_contact_id::text AS created_by_contact_id,
              p.name,
              p.pet_type,
              p.breed,
              p.gender::text AS gender,
              p.date_of_birth,
              p.vaccination_status::text AS vaccination_status,
              p.photo_paths,
              p.status::text AS status,
              p.removal_reason,
              p.removed_by_contact_id::text AS removed_by_contact_id,
              p.deleted_at,
              p.sort_order,
              p.created_at,
              p.updated_at
"""

_CREATED_BY_JOIN = """
LEFT JOIN contacts creator
    ON creator.id = p.created_by_contact_id
   AND creator.organization_id = p.organization_id
LEFT JOIN contact_units creator_cu
    ON creator_cu.contact_id = p.created_by_contact_id
   AND creator_cu.unit_id = p.unit_id
   AND creator_cu.organization_id = p.organization_id
   AND creator_cu.status = 'active'::contact_unit_status
"""

_CREATED_BY_SELECT = """
              creator.prefix AS creator_prefix,
              creator.first_name AS creator_first_name,
              creator.last_name AS creator_last_name,
              creator.profile_photo_url AS creator_profile_photo_url,
              creator_cu.relationship::text AS creator_relationship
"""

_UNIT_JOIN = """
LEFT JOIN units u
    ON u.id = p.unit_id
   AND u.organization_id = p.organization_id
"""

_UNIT_SELECT = """
              u.code AS unit_code,
              u.unit_label
"""

_TOWER_JOIN = """
LEFT JOIN towers t
    ON t.id = u.tower_id
   AND t.organization_id = p.organization_id
"""

_TOWER_SELECT = """
              t.id::text AS tower_id,
              t.name AS tower_name
"""

_ACTIVE_PET_FILTER = f"p.status = '{PetStatus.ACTIVE.value}'::pet_status AND p.deleted_at IS NULL"
_REMOVED_PET_FILTER = f"p.status = '{PetStatus.REMOVED.value}'::pet_status"


class PetsRepository(BaseRepository):
    """Database operations for public.pets."""

    async def create(
        self,
        *,
        organization_id: str,
        project_id: str,
        unit_id: str,
        created_by_contact_id: str,
        name: str,
        pet_type: str,
        breed: str,
        gender: str | None,
        date_of_birth,
        vaccination_status: str,
        photo_paths: list[str],
    ) -> dict[str, Any]:
        """Insert a pet profile."""
        row = await self.db_connection.fetchrow(
            f"""
            INSERT INTO pets AS p (
                organization_id,
                project_id,
                unit_id,
                created_by_contact_id,
                name,
                pet_type,
                breed,
                gender,
                date_of_birth,
                vaccination_status,
                photo_paths,
                status
            )
            VALUES (
                $1::uuid,
                $2::uuid,
                $3::uuid,
                $4::uuid,
                $5,
                $6,
                $7,
                $8::pet_gender,
                $9::date,
                $10::pet_vaccination_status,
                $11::text[],
                '{PetStatus.ACTIVE.value}'::pet_status
            )
            RETURNING
              {_PET_SELECT_COLUMNS}
            """,
            organization_id,
            project_id,
            unit_id,
            created_by_contact_id,
            name,
            pet_type,
            breed,
            gender,
            date_of_birth,
            vaccination_status,
            photo_paths,
        )
        return dict(row)

    async def update(
        self,
        *,
        organization_id: str,
        pet_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch a pet row."""
        if not update_data:
            return await self.get_by_id(organization_id=organization_id, pet_id=pet_id)

        set_parts: list[str] = []
        values: list[Any] = [organization_id, pet_id]
        idx = 3
        enum_casts = {
            "gender": "::pet_gender",
            "vaccination_status": "::pet_vaccination_status",
        }
        for key, value in update_data.items():
            cast = enum_casts.get(key, "")
            set_parts.append(f"{key} = ${idx}{cast}")
            values.append(value)
            idx += 1
        set_parts.append("updated_at = now()")

        row = await self.db_connection.fetchrow(
            f"""
            UPDATE pets p
            SET {", ".join(set_parts)}
            WHERE p.organization_id = $1::uuid
              AND p.id = $2::uuid
              AND {_ACTIVE_PET_FILTER}
            RETURNING
              {_PET_SELECT_COLUMNS}
            """,
            *values,
        )
        return dict(row) if row else None

    async def soft_remove_all_active_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
        reason: str,
        removed_by_contact_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Soft-remove every active pet profile on a unit."""
        rows = await self.db_connection.fetch(
            f"""
            UPDATE pets p
            SET status = '{PetStatus.REMOVED.value}'::pet_status,
                removal_reason = $3,
                removed_by_contact_id = $4::uuid,
                deleted_at = now(),
                updated_at = now()
            WHERE p.organization_id = $1::uuid
              AND p.unit_id = $2::uuid
              AND {_ACTIVE_PET_FILTER}
            RETURNING
              {_PET_SELECT_COLUMNS}
            """,
            organization_id,
            unit_id,
            reason,
            removed_by_contact_id,
        )
        return [dict(row) for row in rows]

    async def soft_remove(
        self,
        *,
        organization_id: str,
        pet_id: str,
        reason: str,
        removed_by_contact_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Soft-remove a pet profile."""
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE pets p
            SET status = '{PetStatus.REMOVED.value}'::pet_status,
                removal_reason = $3,
                removed_by_contact_id = $4::uuid,
                deleted_at = now(),
                updated_at = now()
            WHERE p.organization_id = $1::uuid
              AND p.id = $2::uuid
              AND {_ACTIVE_PET_FILTER}
            RETURNING
              {_PET_SELECT_COLUMNS}
            """,
            organization_id,
            pet_id,
            reason,
            removed_by_contact_id,
        )
        return dict(row) if row else None

    async def get_by_id(
        self,
        *,
        organization_id: str,
        pet_id: str,
        project_id: str | None = None,
        active_only: bool = True,
    ) -> dict[str, Any] | None:
        """Load one pet with creator and unit summary."""
        status_filter = f"AND {_ACTIVE_PET_FILTER}" if active_only else ""
        project_filter = "AND p.project_id = $3::uuid" if project_id else ""
        args: list[Any] = [organization_id, pet_id]
        if project_id:
            args.append(project_id)
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
              {_PET_SELECT_COLUMNS},
              {_CREATED_BY_SELECT},
              {_UNIT_SELECT},
              {_TOWER_SELECT}
            FROM pets p
            {_CREATED_BY_JOIN}
            {_UNIT_JOIN}
            {_TOWER_JOIN}
            WHERE p.organization_id = $1::uuid
              AND p.id = $2::uuid
              {project_filter}
              {status_filter}
            LIMIT 1
            """,
            *args,
        )
        return dict(row) if row else None

    def _project_list_filters(
        self,
        *,
        search: str | None,
        unit_id: str | None,
        tower_id: str | None,
        pet_type: str | None,
        breed: str | None,
        status: str,
        start_index: int,
    ) -> tuple[str, list[Any], int]:
        """Build dynamic WHERE clauses for project-scoped pet list/count."""
        clauses: list[str] = []
        values: list[Any] = []
        idx = start_index

        if status == PetStatus.ACTIVE.value:
            clauses.append(_ACTIVE_PET_FILTER)
        elif status == PetStatus.REMOVED.value:
            clauses.append(_REMOVED_PET_FILTER)

        if unit_id:
            clauses.append(f"p.unit_id = ${idx}::uuid")
            values.append(unit_id)
            idx += 1

        if tower_id:
            clauses.append(f"u.tower_id = ${idx}::uuid")
            values.append(tower_id)
            idx += 1

        if pet_type:
            clauses.append(f"lower(p.pet_type) = lower(${idx})")
            values.append(pet_type.strip())
            idx += 1

        if breed:
            clauses.append(f"lower(p.breed) = lower(${idx})")
            values.append(breed.strip())
            idx += 1

        if search:
            term = f"%{search.strip()}%"
            clauses.append(
                f"(p.name ILIKE ${idx} OR u.code ILIKE ${idx} OR u.unit_label ILIKE ${idx})"
            )
            values.append(term)
            idx += 1

        where_sql = f"AND {' AND '.join(clauses)}" if clauses else ""
        return where_sql, values, idx

    async def list_for_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        search: str | None = None,
        unit_id: str | None = None,
        tower_id: str | None = None,
        pet_type: str | None = None,
        breed: str | None = None,
        status: str = PetStatus.ACTIVE.value,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """List pets in a project with admin registry filters."""
        offset = (page - 1) * page_size
        filter_sql, filter_values, next_param = self._project_list_filters(
            search=search,
            unit_id=unit_id,
            tower_id=tower_id,
            pet_type=pet_type,
            breed=breed,
            status=status,
            start_index=4,
        )
        total = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)::int
            FROM pets p
            {_UNIT_JOIN}
            {_TOWER_JOIN}
            WHERE p.organization_id = $1::uuid
              AND p.project_id = $2::uuid
              {filter_sql}
            """,
            organization_id,
            project_id,
            *filter_values,
        )
        rows = await self.db_connection.fetch(
            f"""
            SELECT
              {_PET_SELECT_COLUMNS},
              {_CREATED_BY_SELECT},
              {_UNIT_SELECT},
              {_TOWER_SELECT}
            FROM pets p
            {_CREATED_BY_JOIN}
            {_UNIT_JOIN}
            {_TOWER_JOIN}
            WHERE p.organization_id = $1::uuid
              AND p.project_id = $2::uuid
              {filter_sql}
            ORDER BY p.status, p.sort_order, p.created_at DESC
            LIMIT ${next_param}::int OFFSET ${next_param + 1}::int
            """,
            organization_id,
            project_id,
            *filter_values,
            page_size,
            offset,
        )
        return [dict(row) for row in rows], int(total or 0)

    async def get_project_summary(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> dict[str, int]:
        """Return active and total pet counts for a project."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
              COUNT(*) FILTER (WHERE {_ACTIVE_PET_FILTER})::int AS active_count,
              COUNT(*)::int AS total_count
            FROM pets p
            WHERE p.organization_id = $1::uuid
              AND p.project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        if not row:
            return {"active_count": 0, "total_count": 0}
        return {
            "active_count": int(row["active_count"] or 0),
            "total_count": int(row["total_count"] or 0),
        }

    async def list_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """List active pets for a unit with pagination."""
        offset = (page - 1) * page_size
        total = await self.count_active_for_unit(
            organization_id=organization_id,
            unit_id=unit_id,
        )
        rows = await self.db_connection.fetch(
            f"""
            SELECT
              {_PET_SELECT_COLUMNS},
              {_CREATED_BY_SELECT},
              {_UNIT_SELECT}
            FROM pets p
            {_CREATED_BY_JOIN}
            {_UNIT_JOIN}
            WHERE p.organization_id = $1::uuid
              AND p.unit_id = $2::uuid
              AND {_ACTIVE_PET_FILTER}
            ORDER BY p.sort_order, p.created_at
            LIMIT $3::int OFFSET $4::int
            """,
            organization_id,
            unit_id,
            page_size,
            offset,
        )
        return [dict(row) for row in rows], total

    async def count_active_for_unit(
        self,
        *,
        organization_id: str,
        unit_id: str,
    ) -> int:
        """Count active pets on a unit (household summary)."""
        value = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)::int
            FROM pets p
            WHERE p.organization_id = $1::uuid
              AND p.unit_id = $2::uuid
              AND {_ACTIVE_PET_FILTER}
            """,
            organization_id,
            unit_id,
        )
        return int(value or 0)
