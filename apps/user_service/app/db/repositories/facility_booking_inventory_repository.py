"""Persistence for bookable inventory: units, schedules, blocks, closures, maintenance."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.db.repositories.facility_booking_config_repository import (
    decode_jsonb,
)
from apps.user_service.app.schemas.facility_booking_inventory import (
    BookingUnitResponse,
    ClosureResponse,
    MaintenanceWindowResponse,
    SchedulePeriodResponse,
    SlotBlockResponse,
)
from apps.user_service.app.utils.project_serialization import serialize_row

_SCHEDULE_JSONB: frozenset[str] = frozenset({"hours"})
_INVENTORY_UUID_COLUMNS: frozenset[str] = frozenset(
    {
        "organization_id",
        "project_id",
        "facility_id",
        "unit_id",
        "created_by_user_id",
    }
)
_INVENTORY_DATE_COLUMNS: frozenset[str] = frozenset(
    {"starts_on", "ends_on", "closed_on", "on_date"}
)
_RESPONSE_MODELS: dict[str, type[BaseModel]] = {
    "facility_booking_units": BookingUnitResponse,
    "facility_schedule_periods": SchedulePeriodResponse,
    "facility_slot_blocks": SlotBlockResponse,
    "facility_closures": ClosureResponse,
    "facility_maintenance_windows": MaintenanceWindowResponse,
}

# table -> (insert columns, order by)
_TABLES: dict[str, tuple[list[str], str]] = {
    "facility_booking_units": (
        ["name", "tower_id", "floor_id", "room_type", "features", "sort_order", "active"],
        "sort_order, created_at",
    ),
    "facility_schedule_periods": (
        ["name", "starts_on", "ends_on", "hours", "created_by_user_id"],
        "starts_on",
    ),
    "facility_slot_blocks": (
        [
            "unit_id",
            "starts_on",
            "ends_on",
            "from_min",
            "to_min",
            "reason",
            "category",
            "created_by_user_id",
        ],
        "starts_on, from_min",
    ),
    "facility_closures": (["closed_on", "reason", "created_by_user_id"], "closed_on"),
    "facility_maintenance_windows": (
        ["on_date", "from_min", "to_min", "note", "created_by_user_id"],
        "on_date, from_min",
    ),
}

# Date column used to skip history when loading inventory for availability.
_ACTIVE_FROM_FILTER: dict[str, str] = {
    "facility_schedule_periods": "ends_on >= $3::date",
    "facility_slot_blocks": "COALESCE(ends_on, starts_on) >= $3::date",
    "facility_closures": "closed_on >= $3::date",
    "facility_maintenance_windows": "on_date >= $3::date",
}


class FacilityBookingInventoryRepository(BaseRepository):
    """Generic CRUD over the facility-scoped inventory tables."""

    def _decode(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        if table == "facility_schedule_periods":
            return decode_jsonb(row, _SCHEDULE_JSONB)
        return row

    def _serialize_row(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        """Normalize DB row types to the API response shape."""
        decoded = serialize_row(self._decode(table, dict(row)))
        model = _RESPONSE_MODELS.get(table)
        if model is None:
            return decoded
        return model.model_validate(decoded).model_dump(mode="json")

    async def insert(
        self,
        table: str,
        *,
        organization_id: str,
        project_id: str,
        facility_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        columns, _ = _TABLES[table]
        rows = await self.bulk_insert_returning(
            table=table,
            required_columns=["organization_id", "project_id", "facility_id"],
            optional_columns=columns,
            rows=[
                {
                    **data,
                    "organization_id": organization_id,
                    "project_id": project_id,
                    "facility_id": facility_id,
                }
            ],
            jsonb_columns=_SCHEDULE_JSONB,
            uuid_columns=_INVENTORY_UUID_COLUMNS,
            date_columns=_INVENTORY_DATE_COLUMNS,
        )
        return self._serialize_row(table, rows[0])

    async def list_rows(
        self,
        table: str,
        *,
        organization_id: str,
        facility_id: str,
        active_from: date | None = None,
    ) -> list[dict[str, Any]]:
        _, order_by = _TABLES[table]
        conditions = ["organization_id = $1::uuid", "facility_id = $2::uuid"]
        params: list[Any] = [organization_id, facility_id]
        if active_from is not None and table in _ACTIVE_FROM_FILTER:
            conditions.append(_ACTIVE_FROM_FILTER[table])
            params.append(active_from)
        rows = await self.db_connection.fetch(
            f"SELECT * FROM {table} WHERE {' AND '.join(conditions)} ORDER BY {order_by}",
            *params,
        )
        return [self._decode(table, dict(r)) for r in rows]

    async def get(
        self, table: str, *, organization_id: str, facility_id: str, row_id: str
    ) -> dict[str, Any] | None:
        row = await self.db_connection.fetchrow(
            f"""
            SELECT * FROM {table}
            WHERE id = $1::uuid AND organization_id = $2::uuid AND facility_id = $3::uuid
            """,
            row_id,
            organization_id,
            facility_id,
        )
        if not row:
            return None
        return self._serialize_row(table, row)

    async def update(
        self,
        table: str,
        *,
        organization_id: str,
        facility_id: str,
        row_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        n = len(update_data)
        row = await self.update_returning(
            table=table,
            where_sql=(
                f"WHERE id = ${n + 1}::uuid AND organization_id = ${n + 2}::uuid "
                f"AND facility_id = ${n + 3}::uuid"
            ),
            where_params=[row_id, organization_id, facility_id],
            update_data=update_data,
            jsonb_columns=_SCHEDULE_JSONB,
        )
        if not row:
            return None
        return self._serialize_row(table, row)

    async def delete(
        self, table: str, *, organization_id: str, facility_id: str, row_id: str
    ) -> bool:
        result = await self.db_connection.execute(
            f"""
            DELETE FROM {table}
            WHERE id = $1::uuid AND organization_id = $2::uuid AND facility_id = $3::uuid
            """,
            row_id,
            organization_id,
            facility_id,
        )
        return result.upper().endswith(" 1")

    async def count_units(self, *, organization_id: str, facility_id: str) -> int:
        value = await self.db_connection.fetchval(
            """
            SELECT COUNT(*)::int FROM facility_booking_units
            WHERE organization_id = $1::uuid AND facility_id = $2::uuid
            """,
            organization_id,
            facility_id,
        )
        return int(value or 0)

    async def load_all(
        self, *, organization_id: str, facility_id: str, active_from: date | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Load every inventory table for a facility (used to build engine snapshots)."""
        return {
            table: await self.list_rows(
                table,
                organization_id=organization_id,
                facility_id=facility_id,
                active_from=active_from,
            )
            for table in _TABLES
        }
