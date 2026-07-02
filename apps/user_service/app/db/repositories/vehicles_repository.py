"""Vehicle persistence for contact onboarding."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import VehicleStatus


class VehiclesRepository(BaseRepository):
    """Database operations for public.vehicles."""

    async def list_by_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
        status: str = VehicleStatus.ACTIVE.value,
    ) -> list[dict[str, Any]]:
        """List vehicles for a contact."""
        rows = await self.db_connection.fetch(
            """
            SELECT
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
              v.photo_path,
              v.status::text AS status,
              v.sort_order,
              v.created_at,
              v.updated_at
            FROM vehicles v
            WHERE v.organization_id = $1::uuid
              AND v.contact_id = $2::uuid
              AND v.status = $3::vehicle_status
            ORDER BY v.sort_order, v.created_at
            """,
            organization_id,
            contact_id,
            status,
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
            """
            SELECT
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
              v.photo_path,
              v.status::text AS status,
              v.sort_order,
              v.created_at,
              v.updated_at
            FROM vehicles v
            WHERE v.organization_id = $1::uuid
              AND v.contact_id = $2::uuid
              AND v.id = $3::uuid
              AND v.status != $4::vehicle_status
            LIMIT 1
            """,
            organization_id,
            contact_id,
            vehicle_id,
            VehicleStatus.REMOVED.value,
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
        photo_path: str | None,
    ) -> dict[str, Any]:
        """Insert a vehicle."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO vehicles (
                organization_id, project_id, contact_id, unit_id,
                vehicle_type, registration_number, make, model, color, photo_path
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid,
                $5::vehicle_type, $6, $7, $8, $9, $10
            )
            RETURNING
              id::text AS id,
              organization_id::text AS organization_id,
              project_id::text AS project_id,
              contact_id::text AS contact_id,
              unit_id::text AS unit_id,
              vehicle_type::text AS vehicle_type,
              registration_number,
              make, model, color, photo_path,
              status::text AS status,
              sort_order,
              created_at,
              updated_at
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
            photo_path,
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
            if col == "vehicle_type":
                set_parts.append(f"{col} = ${idx}::vehicle_type")
            else:
                set_parts.append(f"{col} = ${idx}")
            values.append(val)
            idx += 1
        set_parts.append("updated_at = now()")
        values.extend([organization_id, contact_id, vehicle_id, VehicleStatus.REMOVED.value])

        row = await self.db_connection.fetchrow(
            f"""
            UPDATE vehicles
            SET {", ".join(set_parts)}
            WHERE organization_id = ${idx}::uuid
              AND contact_id = ${idx + 1}::uuid
              AND id = ${idx + 2}::uuid
              AND status != ${idx + 3}::vehicle_status
            RETURNING
              id::text AS id,
              organization_id::text AS organization_id,
              project_id::text AS project_id,
              contact_id::text AS contact_id,
              unit_id::text AS unit_id,
              vehicle_type::text AS vehicle_type,
              registration_number,
              make, model, color, photo_path,
              status::text AS status,
              sort_order,
              created_at,
              updated_at
            """,
            *values,
        )
        return dict(row) if row else None

    async def soft_remove(
        self,
        *,
        organization_id: str,
        contact_id: str,
        vehicle_id: str,
    ) -> dict[str, Any] | None:
        """Mark vehicle removed."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE vehicles
            SET status = $4::vehicle_status,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND id = $3::uuid
              AND status = $5::vehicle_status
            RETURNING id::text AS id, status::text AS status
            """,
            organization_id,
            contact_id,
            vehicle_id,
            VehicleStatus.REMOVED.value,
            VehicleStatus.ACTIVE.value,
        )
        return dict(row) if row else None

    async def count_active(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> int:
        """Count active vehicles for contact."""
        count = await self.db_connection.fetchval(
            """
            SELECT COUNT(*)
            FROM vehicles
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND status = $3::vehicle_status
            """,
            organization_id,
            contact_id,
            VehicleStatus.ACTIVE.value,
        )
        return int(count or 0)
