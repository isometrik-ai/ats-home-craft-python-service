"""Vehicle persistence for contact onboarding."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.db.repositories.units_repository import (
    _OWNER_PRIMARY_EMAIL_SQL,
    _OWNER_PRIMARY_PHONE_SQL,
    _RESOLVED_CONFIG_KIND_SQL,
    _RESOLVED_PROPERTY_TYPE_SQL,
)

_VEHICLE_ENUM_CASTS: dict[str, str] = {
    "vehicle_type": "::vehicle_type",
    "fuel_type": "::vehicle_fuel_type",
    "status": "::vehicle_status",
}

_ACTIVE_VEHICLE_FILTER = "v.deleted_at IS NULL"

_VEHICLE_OWNER_LATERAL_JOIN = f"""
LEFT JOIN LATERAL (
    SELECT
        c.id AS owner_contact_id,
        c.prefix AS owner_prefix,
        c.first_name AS owner_first_name,
        c.last_name AS owner_last_name,
        c.phones AS owner_phones,
        c.emails AS owner_emails,
        c.profile_photo_url AS owner_profile_photo_url,
        {_OWNER_PRIMARY_PHONE_SQL} AS owner_primary_phone,
        {_OWNER_PRIMARY_EMAIL_SQL} AS owner_primary_email
    FROM contact_units cu
    JOIN contacts c
        ON c.id = cu.contact_id
       AND c.organization_id = cu.organization_id
    WHERE cu.organization_id = v.organization_id
      AND cu.unit_id = v.unit_id
      AND cu.status IN (
          'active'::contact_unit_status,
          'pending'::contact_unit_status
      )
      AND c.status = 'active'
      AND c.contact_type = 'Owner'
    ORDER BY cu.is_primary DESC, cu.sort_order, cu.created_at
    LIMIT 1
) owner_row ON TRUE
"""

_VEHICLE_OWNER_SELECT_COLUMNS = """
              owner_row.owner_contact_id::text AS owner_contact_id,
              owner_row.owner_prefix,
              owner_row.owner_first_name,
              owner_row.owner_last_name,
              owner_row.owner_phones,
              owner_row.owner_emails,
              owner_row.owner_primary_phone,
              owner_row.owner_primary_email,
              owner_row.owner_profile_photo_url
"""

_VEHICLE_UNIT_JOINS = """
LEFT JOIN units u
    ON u.id = v.unit_id
   AND u.organization_id = v.organization_id
LEFT JOIN towers t
    ON t.id = u.tower_id
   AND t.organization_id = u.organization_id
LEFT JOIN floors f
    ON f.id = u.floor_id
   AND f.organization_id = u.organization_id
LEFT JOIN unit_configs uc
    ON uc.id = u.config_id
   AND uc.organization_id = u.organization_id
LEFT JOIN plot_config_items pci
    ON pci.id = u.plot_item_id
   AND pci.organization_id = u.organization_id
"""

_VEHICLE_UNIT_SELECT_COLUMNS = f"""
              u.code AS unit_code,
              u.unit_label,
              u.status::text AS unit_status,
              u.tower_id::text AS unit_tower_id,
              u.config_id::text AS unit_config_id,
              u.plot_item_id::text AS unit_plot_item_id,
              u.sort_order AS unit_sort_order,
              t.name AS unit_tower_name,
              t.tower_type AS unit_tower_type,
              f.display_name AS unit_floor_display_name,
              f.level_number AS unit_floor_level_number,
              uc.config_kind::text AS unit_config_kind,
              uc.display_label AS unit_config_display_label,
              uc.name AS unit_config_name,
              pci.description AS unit_plot_description,
              {_RESOLVED_PROPERTY_TYPE_SQL} AS unit_resolved_property_type,
              {_RESOLVED_CONFIG_KIND_SQL} AS unit_resolved_config_kind
"""


class VehiclesRepository(BaseRepository):
    """Database operations for public.vehicles."""

    _VEHICLE_SELECT_COLUMNS = """
              v.id::text AS id,
              v.organization_id::text AS organization_id,
              v.project_id::text AS project_id,
              v.contact_id::text AS contact_id,
              v.unit_id::text AS unit_id,
              v.vehicle_type::text AS vehicle_type,
              v.registration_number,
              v.make,
              v.model,
              v.color,
              v.photo_paths,
              v.fuel_type::text AS fuel_type,
              v.status::text AS status,
              v.rejection_reason,
              v.parking_slot_id::text AS parking_slot_id,
              v.status_updated_at,
              v.sort_order,
              v.created_at,
              v.updated_at
    """

    _VEHICLE_RETURNING_COLUMNS = """
              id::text AS id,
              organization_id::text AS organization_id,
              project_id::text AS project_id,
              contact_id::text AS contact_id,
              unit_id::text AS unit_id,
              vehicle_type::text AS vehicle_type,
              registration_number,
              make,
              model,
              color,
              photo_paths,
              fuel_type::text AS fuel_type,
              status::text AS status,
              rejection_reason,
              parking_slot_id::text AS parking_slot_id,
              status_updated_at,
              sort_order,
              created_at,
              updated_at
    """

    async def list_by_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
        unit_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List active (non-soft-deleted) vehicles for a contact."""
        rows = await self.db_connection.fetch(
            f"""
            SELECT
              {self._VEHICLE_SELECT_COLUMNS}
            FROM vehicles v
            WHERE v.organization_id = $1::uuid
              AND v.contact_id = $2::uuid
              AND ($3::uuid IS NULL OR v.unit_id = $3::uuid)
              AND {_ACTIVE_VEHICLE_FILTER}
            ORDER BY v.sort_order, v.created_at
            """,
            organization_id,
            contact_id,
            unit_id,
        )
        return [dict(row) for row in rows]

    async def get_by_id(
        self,
        *,
        organization_id: str,
        contact_id: str,
        vehicle_id: str,
        include_removed: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch one vehicle owned by contact."""
        removed_filter = "" if include_removed else f"AND {_ACTIVE_VEHICLE_FILTER}"
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
              {self._VEHICLE_SELECT_COLUMNS}
            FROM vehicles v
            WHERE v.organization_id = $1::uuid
              AND v.contact_id = $2::uuid
              AND v.id = $3::uuid
              {removed_filter}
            LIMIT 1
            """,
            organization_id,
            contact_id,
            vehicle_id,
        )
        return dict(row) if row else None

    async def create(
        self,
        *,
        organization_id: str,
        project_id: str,
        contact_id: str,
        unit_id: str,
        vehicle_type: str,
        registration_number: str,
        make: str | None,
        model: str | None,
        color: str | None,
        photo_paths: list[str],
        fuel_type: str | None,
    ) -> dict[str, Any]:
        """Insert a vehicle."""
        row = await self.db_connection.fetchrow(
            f"""
            INSERT INTO vehicles (
                organization_id, project_id, contact_id, unit_id,
                vehicle_type, registration_number, make, model, color,
                photo_paths, fuel_type, status_updated_at
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid,
                $5::vehicle_type, $6, $7, $8, $9,
                $10::text[], $11::vehicle_fuel_type, now()
            )
            RETURNING
              {self._VEHICLE_RETURNING_COLUMNS}
            """,
            organization_id,
            project_id,
            contact_id,
            unit_id,
            vehicle_type,
            registration_number,
            make,
            model,
            color,
            photo_paths,
            fuel_type,
        )
        return dict(row)

    async def update(
        self,
        *,
        organization_id: str,
        contact_id: str,
        vehicle_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch vehicle fields."""
        if not update_data:
            return await self.get_by_id(
                organization_id=organization_id,
                contact_id=contact_id,
                vehicle_id=vehicle_id,
            )

        set_parts: list[str] = []
        values: list[Any] = []
        idx = 1
        for col, val in update_data.items():
            if col in {"id", "organization_id", "contact_id", "created_at"}:
                continue
            cast = _VEHICLE_ENUM_CASTS.get(col, "")
            if col == "photo_paths":
                set_parts.append(f"{col} = ${idx}::text[]")
            else:
                set_parts.append(f"{col} = ${idx}{cast}")
            values.append(val)
            idx += 1
        if "status" in update_data:
            set_parts.append("status_updated_at = now()")
        set_parts.append("updated_at = now()")
        values.extend([organization_id, contact_id, vehicle_id])

        row = await self.db_connection.fetchrow(
            f"""
            UPDATE vehicles
            SET {", ".join(set_parts)}
            WHERE organization_id = ${idx}::uuid
              AND contact_id = ${idx + 1}::uuid
              AND id = ${idx + 2}::uuid
              AND deleted_at IS NULL
            RETURNING
              {self._VEHICLE_RETURNING_COLUMNS}
            """,
            *values,
        )
        return dict(row) if row else None

    async def update_by_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        vehicle_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch vehicle fields scoped to a project (admin review)."""
        if not update_data:
            return await self.get_by_project(
                organization_id=organization_id,
                project_id=project_id,
                vehicle_id=vehicle_id,
            )

        set_parts: list[str] = []
        values: list[Any] = []
        idx = 1
        for col, val in update_data.items():
            if col in {"id", "organization_id", "contact_id", "created_at"}:
                continue
            cast = _VEHICLE_ENUM_CASTS.get(col, "")
            if col == "photo_paths":
                set_parts.append(f"{col} = ${idx}::text[]")
            elif col == "parking_slot_id":
                set_parts.append(f"{col} = ${idx}::uuid")
            else:
                set_parts.append(f"{col} = ${idx}{cast}")
            values.append(val)
            idx += 1
        if "status" in update_data:
            set_parts.append("status_updated_at = now()")
        set_parts.append("updated_at = now()")
        values.extend([organization_id, project_id, vehicle_id])

        row = await self.db_connection.fetchrow(
            f"""
            UPDATE vehicles
            SET {", ".join(set_parts)}
            WHERE organization_id = ${idx}::uuid
              AND project_id = ${idx + 1}::uuid
              AND id = ${idx + 2}::uuid
              AND deleted_at IS NULL
            RETURNING
              {self._VEHICLE_RETURNING_COLUMNS}
            """,
            *values,
        )
        return dict(row) if row else None

    async def delete(
        self,
        *,
        organization_id: str,
        contact_id: str,
        vehicle_id: str,
    ) -> dict[str, Any] | None:
        """Hard-delete a vehicle owned by the contact."""
        row = await self.db_connection.fetchrow(
            f"""
            DELETE FROM vehicles
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND id = $3::uuid
            RETURNING
              {self._VEHICLE_RETURNING_COLUMNS}
            """,
            organization_id,
            contact_id,
            vehicle_id,
        )
        return dict(row) if row else None

    async def soft_remove(
        self,
        *,
        organization_id: str,
        contact_id: str,
        vehicle_id: str,
    ) -> dict[str, Any] | None:
        """Soft-delete an approved vehicle (status removed)."""
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE vehicles
            SET status = 'removed'::vehicle_status,
                parking_slot_id = NULL,
                deleted_at = now(),
                status_updated_at = now(),
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND id = $3::uuid
              AND deleted_at IS NULL
            RETURNING
              {self._VEHICLE_RETURNING_COLUMNS}
            """,
            organization_id,
            contact_id,
            vehicle_id,
        )
        return dict(row) if row else None

    async def list_by_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        status: str | None = None,
        include_removed: bool = True,
    ) -> list[dict[str, Any]]:
        """List vehicles for a project (admin view)."""
        removed_filter = "" if include_removed else f"AND {_ACTIVE_VEHICLE_FILTER}"
        rows = await self.db_connection.fetch(
            f"""
            SELECT
              {self._VEHICLE_SELECT_COLUMNS},
              {_VEHICLE_UNIT_SELECT_COLUMNS},
              {_VEHICLE_OWNER_SELECT_COLUMNS}
            FROM vehicles v
            {_VEHICLE_UNIT_JOINS}
            {_VEHICLE_OWNER_LATERAL_JOIN}
            WHERE v.organization_id = $1::uuid
              AND v.project_id = $2::uuid
              AND ($3::vehicle_status IS NULL OR v.status = $3::vehicle_status)
              {removed_filter}
            ORDER BY v.created_at DESC, v.sort_order
            """,
            organization_id,
            project_id,
            status,
        )
        return [dict(row) for row in rows]

    async def get_by_project(
        self,
        *,
        organization_id: str,
        project_id: str,
        vehicle_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one active vehicle in a project (admin view)."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
              {self._VEHICLE_SELECT_COLUMNS}
            FROM vehicles v
            WHERE v.organization_id = $1::uuid
              AND v.project_id = $2::uuid
              AND v.id = $3::uuid
              AND {_ACTIVE_VEHICLE_FILTER}
            LIMIT 1
            """,
            organization_id,
            project_id,
            vehicle_id,
        )
        return dict(row) if row else None

    async def count_active(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> int:
        """Count non-removed vehicles for contact."""
        count = await self.db_connection.fetchval(
            f"""
            SELECT COUNT(*)
            FROM vehicles v
            WHERE v.organization_id = $1::uuid
              AND v.contact_id = $2::uuid
              AND {_ACTIVE_VEHICLE_FILTER}
            """,
            organization_id,
            contact_id,
        )
        return int(count or 0)
