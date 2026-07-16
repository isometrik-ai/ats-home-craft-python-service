"""Facilities persistence."""

from __future__ import annotations

import json
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_FACILITY_COLUMN_CASTS: dict[str, str] = {
    "status": "::facility_status",
    "location_type": "::facility_location_type",
    "parking_user_type": "::parking_user_type",
}

_FACILITY_INSERT_COLUMNS: tuple[str, ...] = (
    "organization_id",
    "project_id",
    "name",
    "status",
    "facility_type",
    "facility_subtype",
    "location_type",
    "tower_id",
    "floor_level",
    "wing",
    "area_sqft",
    "capacity_persons",
    "parking_slots",
    "parking_user_type",
    "extra_attributes",
    "location_notes",
    "latitude",
    "longitude",
    "active",
    "sort_order",
)


class FacilitiesRepository(BaseRepository):
    """Database operations for public.facilities."""

    def _prepare_value(self, col: str, val: Any) -> Any:
        """Serialize values that need DB casts."""
        if col == "extra_attributes":
            return json.dumps(val if val is not None else {})
        return val

    async def insert_facility(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a facility row."""
        present = [col for col in _FACILITY_INSERT_COLUMNS if col in data]
        col_sql = ", ".join(present)
        placeholders: list[str] = []
        values: list[Any] = []
        for idx, col in enumerate(present, start=1):
            if col == "extra_attributes":
                placeholders.append(f"${idx}::jsonb")
            else:
                placeholders.append(f"${idx}{_FACILITY_COLUMN_CASTS.get(col, '')}")
            values.append(self._prepare_value(col, data.get(col)))
        row = await self.db_connection.fetchrow(
            f"INSERT INTO facilities ({col_sql}) VALUES ({', '.join(placeholders)}) RETURNING *",
            *values,
        )
        return dict(row)

    async def get_facility(
        self, *, organization_id: str, project_id: str, facility_id: str
    ) -> dict[str, Any] | None:
        """Fetch a facility scoped to org + project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT * FROM facilities
            WHERE id = $1::uuid AND project_id = $2::uuid AND organization_id = $3::uuid
            """,
            facility_id,
            project_id,
            organization_id,
        )
        return dict(row) if row else None

    async def list_facilities(
        self, *, organization_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """List facilities for a project."""
        rows = await self.db_connection.fetch(
            """
            SELECT * FROM facilities
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            ORDER BY sort_order, created_at
            """,
            organization_id,
            project_id,
        )
        return [dict(row) for row in rows]

    async def update_facility(
        self,
        *,
        organization_id: str,
        project_id: str,
        facility_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch a facility."""
        if not update_data:
            return await self.get_facility(
                organization_id=organization_id,
                project_id=project_id,
                facility_id=facility_id,
            )
        set_parts: list[str] = []
        values: list[Any] = []
        idx = 1
        for col, val in update_data.items():
            if col == "extra_attributes":
                set_parts.append(f"{col} = ${idx}::jsonb")
                values.append(json.dumps(val if val is not None else {}))
            else:
                set_parts.append(f"{col} = ${idx}{_FACILITY_COLUMN_CASTS.get(col, '')}")
                values.append(val)
            idx += 1
        set_parts.append("updated_at = now()")
        values.extend([facility_id, project_id, organization_id])
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE facilities SET {", ".join(set_parts)}
            WHERE id = ${idx}::uuid AND project_id = ${idx + 1}::uuid
              AND organization_id = ${idx + 2}::uuid
            RETURNING *
            """,
            *values,
        )
        return dict(row) if row else None

    async def delete_facility(
        self, *, organization_id: str, project_id: str, facility_id: str
    ) -> bool:
        """Delete a facility."""
        result = await self.db_connection.execute(
            """
            DELETE FROM facilities
            WHERE id = $1::uuid AND project_id = $2::uuid AND organization_id = $3::uuid
            """,
            facility_id,
            project_id,
            organization_id,
        )
        return result.upper().endswith("1")
