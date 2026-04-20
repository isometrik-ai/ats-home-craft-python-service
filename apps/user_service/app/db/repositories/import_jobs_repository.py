"""Import jobs repository (producer side; generalized ``import_jobs`` table)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ContactsImportType


class ImportJobsRepository(BaseRepository):
    """Repository for ``import_jobs`` rows (org-scoped; ``import_type`` selects pipeline).

    Columns follow ``docs/contacts-import-schema.md``:
    - ``job_key`` — public id for API paths (e.g. ``imp_...``)
    - ``import_type`` — e.g. ``contacts``
    - Progress: ``total_rows``, ``processed_rows``, ``success_rows``, ``error_rows``
    """

    TABLE = "import_jobs"
    JSONB_COLUMNS = frozenset({"mapping", "options"})

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        super().__init__(db_connection=db_connection)

    @staticmethod
    def _normalize_job_row(row: dict[str, Any]) -> dict[str, Any]:
        """Expose API ``job_id`` alongside DB ``job_key`` (same value)."""
        out = dict(row)
        out["job_id"] = str(out["job_key"])
        return out

    async def create_job(
        self,
        *,
        job_id: str,
        organization_id: str,
        status: str,
        file_url: str,
        file_type: str,
        schema_version: int,
        mapping: dict[str, Any] | None,
        options: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Insert a new import job row and return its normalized representation."""
        rows = [
            {
                "organization_id": organization_id,
                "job_key": job_id,
                "import_type": ContactsImportType.CONTACTS.value,
                "status": status,
                "file_url": file_url,
                "file_type": file_type,
                "schema_version": schema_version,
                "mapping": mapping if mapping is not None else {},
                "options": options if options is not None else {},
                "total_rows": 0,
                "processed_rows": 0,
                "success_rows": 0,
                "error_rows": 0,
                "errors_file_url": None,
            }
        ]
        inserted = await self.bulk_insert_returning(
            table=self.TABLE,
            required_columns=[
                "organization_id",
                "job_key",
                "import_type",
                "status",
                "file_url",
                "file_type",
                "schema_version",
            ],
            optional_columns=[
                "mapping",
                "options",
                "total_rows",
                "processed_rows",
                "success_rows",
                "error_rows",
                "errors_file_url",
            ],
            rows=rows,
            jsonb_columns=self.JSONB_COLUMNS,
        )
        return self._normalize_job_row(inserted[0])

    async def get_job(
        self,
        *,
        job_id: str,
        organization_id: str,
    ) -> dict[str, Any] | None:
        """Fetch a single import job for an organization by its public job key."""
        query = f"""
            SELECT
                id,
                organization_id,
                job_key,
                import_type,
                status,
                file_url,
                file_type,
                schema_version,
                mapping,
                options,
                total_rows,
                processed_rows,
                success_rows,
                error_rows,
                errors_file_url,
                created_at,
                started_at,
                finished_at,
                updated_at
            FROM {self.TABLE}
            WHERE job_key = $1 AND organization_id = $2
        """
        row = await self.db_connection.fetchrow(query, job_id, organization_id)
        return self._normalize_job_row(dict(row)) if row else None

    async def set_status(
        self,
        *,
        job_id: str,
        organization_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        """Update the status of an import job and return the updated row."""
        updated = await self.update_returning(
            table=self.TABLE,
            where_sql="WHERE job_key = $2 AND organization_id = $3",
            where_params=[job_id, organization_id],
            update_data={"status": status},
            jsonb_columns=self.JSONB_COLUMNS,
            touch_updated_at=True,
        )
        return self._normalize_job_row(updated) if updated else None

    async def set_status_and_timestamps(
        self,
        *,
        job_id: str,
        organization_id: str,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Update job status and optionally set started/finished timestamps.

        Note: we cast timestamps explicitly to avoid asyncpg prepared-statement
        cache type mismatches across pooled connections.
        """
        set_parts: list[str] = ["status = $1"]
        params: list[Any] = [status]
        idx = 2

        if started_at is not None:
            set_parts.append(f"started_at = ${idx}::timestamptz")
            params.append(started_at)
            idx += 1

        if finished_at is not None:
            set_parts.append(f"finished_at = ${idx}::timestamptz")
            params.append(finished_at)
            idx += 1

        set_parts.append("updated_at = NOW()")

        query = f"""
            UPDATE {self.TABLE}
            SET {", ".join(set_parts)}
            WHERE job_key = ${idx} AND organization_id = ${idx + 1}
            RETURNING *
        """
        params.extend([job_id, organization_id])
        row = await self.db_connection.fetchrow(query, *params)
        updated = dict(row) if row else None
        return self._normalize_job_row(updated) if updated else None

    async def increment_counters(
        self,
        *,
        job_id: str,
        organization_id: str,
        total_rows_delta: int,
        processed_rows_delta: int,
        success_rows_delta: int,
        error_rows_delta: int,
    ) -> dict[str, Any] | None:
        """Atomically increment progress counters for an import job.

        This keeps polling cheap and ensures that concurrent updates from
        different batches do not overwrite each other.
        """
        query = f"""
            UPDATE {self.TABLE}
            SET
                total_rows = total_rows + $3,
                processed_rows = processed_rows + $4,
                success_rows = success_rows + $5,
                error_rows = error_rows + $6,
                updated_at = NOW()
            WHERE job_key = $1 AND organization_id = $2
            RETURNING *
        """
        row = await self.db_connection.fetchrow(
            query,
            job_id,
            organization_id,
            total_rows_delta,
            processed_rows_delta,
            success_rows_delta,
            error_rows_delta,
        )
        return self._normalize_job_row(dict(row)) if row else None
