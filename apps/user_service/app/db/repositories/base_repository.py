"""Generic repository helpers for asyncpg repositories.

Keep SQL building centralized and safe:
- multi-row INSERT with positional parameters
- dynamic UPDATE that only touches provided fields

All values are passed as parameters (no string interpolation of user input).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import asyncpg

from apps.user_service.app.utils.common_utils import serialize_jsonb_param


def _row_columns_present(rows: Iterable[dict[str, Any]], columns: Iterable[str]) -> list[str]:
    """Check which columns are present in the rows."""
    present: set[str] = set()
    allowed = list(columns)
    for row in rows:
        if not isinstance(row, dict):
            continue
        for col in allowed:
            if col in row:
                present.add(col)
    return [c for c in allowed if c in present]


class BaseRepository:
    """Base repository with small, reusable SQL helpers."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    async def bulk_insert_returning(
        self,
        *,
        table: str,
        required_columns: list[str],
        optional_columns: list[str],
        rows: list[dict[str, Any]],
        jsonb_columns: frozenset[str] = frozenset(),
        on_conflict_sql: str | None = None,
    ) -> list[dict[str, Any]]:
        """Insert many rows in one statement and RETURNING *.

        Only columns present in at least one row are included (besides required).
        """
        if not rows:
            return []

        columns = list(required_columns) + _row_columns_present(rows, optional_columns)
        ncols = len(columns)
        placeholders = [
            "(" + ", ".join(f"${i * ncols + j + 1}" for j in range(ncols)) + ")"
            for i in range(len(rows))
        ]

        values_flat: list[Any] = []
        for row in rows:
            for col in columns:
                values_flat.append(serialize_jsonb_param(col, row.get(col), jsonb_columns))

        conflict_clause = (
            f" {on_conflict_sql.strip()} " if on_conflict_sql and on_conflict_sql.strip() else " "
        )
        query = (
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES {', '.join(placeholders)}"
            f"{conflict_clause}"
            "RETURNING *"
        )
        records = await self.db_connection.fetch(query, *values_flat)
        return [dict(r) for r in records]

    async def update_returning(
        self,
        *,
        table: str,
        where_sql: str,
        where_params: list[Any],
        update_data: dict[str, Any],
        jsonb_columns: frozenset[str] = frozenset(),
        touch_updated_at: bool = True,
    ) -> dict[str, Any] | None:
        """Dynamic UPDATE ... RETURNING *.

        `where_sql` must be a SQL fragment that starts with 'WHERE ...' and uses
        positional parameters AFTER the update params.
        """
        if not update_data and not touch_updated_at:
            return None

        set_parts: list[str] = []
        params: list[Any] = []
        idx = 1
        for key, value in update_data.items():
            cast = "::jsonb" if key in jsonb_columns else ""
            set_parts.append(f"{key} = ${idx}{cast}")
            params.append(serialize_jsonb_param(key, value, jsonb_columns))
            idx += 1

        if touch_updated_at:
            set_parts.append("updated_at = NOW()")

        query = f"UPDATE {table} SET {', '.join(set_parts)} {where_sql} RETURNING *"
        row = await self.db_connection.fetchrow(query, *(params + list(where_params)))
        return dict(row) if row else None
