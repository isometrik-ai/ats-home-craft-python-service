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

_ACTIVE_PET_FILTER = f"p.status = '{PetStatus.ACTIVE.value}'::pet_status AND p.deleted_at IS NULL"


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
        removed_by_contact_id: str,
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
        active_only: bool = True,
    ) -> dict[str, Any] | None:
        """Load one pet with creator and unit summary."""
        status_filter = f"AND {_ACTIVE_PET_FILTER}" if active_only else ""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
              {_PET_SELECT_COLUMNS},
              {_CREATED_BY_SELECT},
              {_UNIT_SELECT}
            FROM pets p
            {_CREATED_BY_JOIN}
            {_UNIT_JOIN}
            WHERE p.organization_id = $1::uuid
              AND p.id = $2::uuid
              {status_filter}
            LIMIT 1
            """,
            organization_id,
            pet_id,
        )
        return dict(row) if row else None

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
