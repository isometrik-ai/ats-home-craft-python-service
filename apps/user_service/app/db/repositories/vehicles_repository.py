"""Vehicle persistence for contact onboarding."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_VEHICLE_ENUM_CASTS: dict[str, str] = {
    "vehicle_type": "::vehicle_type",
    "fuel_type": "::vehicle_fuel_type",
    "status": "::vehicle_status",
}


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
              sort_order,
              created_at,
              updated_at
    """

    async def list_by_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> list[dict[str, Any]]:
        """List vehicles for a contact."""
        rows = await self.db_connection.fetch(
            f"""
            SELECT
              {self._VEHICLE_SELECT_COLUMNS}
            FROM vehicles v
            WHERE v.organization_id = $1::uuid
              AND v.contact_id = $2::uuid
            ORDER BY v.sort_order, v.created_at
            """,
            organization_id,
            contact_id,
        )
        return [dict(row) for row in rows]

    async def get_by_id(
        self,
        *,
        organization_id: str,
        contact_id: str,
        vehicle_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one vehicle owned by contact."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT
              {self._VEHICLE_SELECT_COLUMNS}
            FROM vehicles v
            WHERE v.organization_id = $1::uuid
              AND v.contact_id = $2::uuid
              AND v.id = $3::uuid
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
                photo_paths, fuel_type
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid,
                $5::vehicle_type, $6, $7, $8, $9,
                $10::text[], $11::vehicle_fuel_type
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
        set_parts.append("updated_at = now()")
        values.extend([organization_id, contact_id, vehicle_id])

        row = await self.db_connection.fetchrow(
            f"""
            UPDATE vehicles
            SET {", ".join(set_parts)}
            WHERE organization_id = ${idx}::uuid
              AND contact_id = ${idx + 1}::uuid
              AND id = ${idx + 2}::uuid
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

    async def count_active(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> int:
        """Count vehicles for contact."""
        count = await self.db_connection.fetchval(
            """
            SELECT COUNT(*)
            FROM vehicles
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
            """,
            organization_id,
            contact_id,
        )
        return int(count or 0)
