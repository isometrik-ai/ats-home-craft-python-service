"""Import job rows repository (row ledger for async imports)."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository


class ImportJobRowsRepository(BaseRepository):
    """Repository for `public.import_job_rows` (org-scoped row ledger)."""

    TABLE = "import_job_rows"

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        super().__init__(db_connection=db_connection)

    async def claim_rows_processing(
        self,
        *,
        organization_id: str,
        job_id: str,
        rows: list[tuple[int, dict[str, Any] | None]],
    ) -> dict[int, str]:
        """Batch-claim rows as `processing` and return current statuses by row_number.

        This is the high-throughput variant used by the consumer to minimize DB
        round trips. Rows that already exist keep their existing status.
        """
        if not rows:
            return {}

        row_numbers = [int(rn) for rn, _ in rows]
        raw_rows = [raw for _, raw in rows]

        await self.db_connection.execute(
            """
            INSERT INTO import_job_rows (organization_id, job_id, row_number, status, raw_row)
            SELECT $1::uuid, $2::uuid, u.row_number, 'processing', u.raw_row
            FROM unnest($3::int[], $4::jsonb[]) AS u(row_number, raw_row)
            ON CONFLICT (job_id, row_number) DO NOTHING
            """,
            organization_id,
            job_id,
            row_numbers,
            raw_rows,
        )

        fetched = await self.db_connection.fetch(
            """
            SELECT row_number, status
            FROM import_job_rows
            WHERE organization_id = $1::uuid
              AND job_id = $2::uuid
              AND row_number = ANY($3::int[])
            """,
            organization_id,
            job_id,
            row_numbers,
        )
        return {int(r["row_number"]): str(r["status"]) for r in fetched}

    async def claim_row_processing(
        self,
        *,
        organization_id: str,
        job_id: str,
        row_number: int,
        raw_row: dict[str, Any] | None = None,
    ) -> str:
        """Insert a row-ledger record with `processing` status (idempotent).

        Returns the current row status after the operation:
        - "processing" when claimed now
        - existing status when already present
        """
        inserted = await self.db_connection.fetchrow(
            """
            INSERT INTO import_job_rows (organization_id, job_id, row_number, status, raw_row)
            VALUES ($1::uuid, $2::uuid, $3::int, 'processing', $4::jsonb)
            ON CONFLICT (job_id, row_number) DO NOTHING
            RETURNING status
            """,
            organization_id,
            job_id,
            row_number,
            raw_row,
        )
        if inserted and inserted.get("status"):
            return str(inserted["status"])

        existing = await self.db_connection.fetchrow(
            """
            SELECT status
            FROM import_job_rows
            WHERE organization_id = $1::uuid AND job_id = $2::uuid AND row_number = $3::int
            """,
            organization_id,
            job_id,
            row_number,
        )
        return str(existing["status"]) if existing and existing.get("status") else "processing"

    async def mark_success_bulk(
        self,
        *,
        organization_id: str,
        job_id: str,
        row_numbers: list[int],
    ) -> None:
        """Mark multiple row-ledger entries as successful in one statement."""
        if not row_numbers:
            return
        await self.db_connection.execute(
            """
            UPDATE import_job_rows
            SET status = 'success', error_code = NULL, error_message = NULL, updated_at = NOW()
            WHERE organization_id = $1::uuid
              AND job_id = $2::uuid
              AND row_number = ANY($3::int[])
            """,
            organization_id,
            job_id,
            [int(rn) for rn in row_numbers],
        )

    async def mark_success(
        self,
        *,
        organization_id: str,
        job_id: str,
        row_number: int,
    ) -> None:
        """Mark a single row-ledger entry as successful."""
        await self.db_connection.execute(
            """
            UPDATE import_job_rows
            SET status = 'success', error_code = NULL, error_message = NULL, updated_at = NOW()
            WHERE organization_id = $1::uuid AND job_id = $2::uuid AND row_number = $3::int
            """,
            organization_id,
            job_id,
            row_number,
        )

    async def mark_errors_bulk(
        self,
        *,
        organization_id: str,
        job_id: str,
        errors: list[tuple[int, str, str, dict[str, Any] | None]],
    ) -> None:
        """Mark multiple row-ledger entries as errors in one statement."""
        if not errors:
            return

        row_numbers = [int(rn) for rn, _, _, _ in errors]
        error_codes = [str(code) for _, code, _, _ in errors]
        error_messages = [str(msg) for _, _, msg, _ in errors]
        raw_rows = [raw for _, _, _, raw in errors]

        await self.db_connection.execute(
            """
            UPDATE import_job_rows r
            SET
              status = 'error',
              error_code = u.error_code,
              error_message = u.error_message,
              raw_row = COALESCE(u.raw_row, r.raw_row),
              updated_at = NOW()
            FROM unnest($3::int[], $4::text[], $5::text[], $6::jsonb[])
              AS u(row_number, error_code, error_message, raw_row)
            WHERE r.organization_id = $1::uuid
              AND r.job_id = $2::uuid
              AND r.row_number = u.row_number
            """,
            organization_id,
            job_id,
            row_numbers,
            error_codes,
            error_messages,
            raw_rows,
        )

    async def mark_error(
        self,
        *,
        organization_id: str,
        job_id: str,
        row_number: int,
        error_code: str,
        error_message: str,
        raw_row: dict[str, Any] | None = None,
    ) -> None:
        """Mark a single row-ledger entry as error, optionally persisting raw_row."""
        await self.db_connection.execute(
            """
            UPDATE import_job_rows
            SET
              status = 'error',
              error_code = $4::text,
              error_message = $5::text,
              raw_row = COALESCE($6::jsonb, raw_row),
              updated_at = NOW()
            WHERE organization_id = $1::uuid AND job_id = $2::uuid AND row_number = $3::int
            """,
            organization_id,
            job_id,
            row_number,
            error_code,
            error_message,
            raw_row,
        )

    async def list_error_rows(
        self,
        *,
        organization_id: str,
        job_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List row-level errors for a job (paginated)."""
        page = max(int(page or 1), 1)
        page_size = max(int(page_size or 1), 1)
        offset = (page - 1) * page_size

        total = await self.db_connection.fetchval(
            """
            SELECT COUNT(*)::int
            FROM import_job_rows
            WHERE organization_id = $1::uuid
              AND job_id = $2::uuid
              AND status = 'error'
            """,
            organization_id,
            job_id,
        )

        fetched = await self.db_connection.fetch(
            """
            SELECT
              row_number,
              error_code,
              error_message,
              raw_row,
              updated_at
            FROM import_job_rows
            WHERE organization_id = $1::uuid
              AND job_id = $2::uuid
              AND status = 'error'
            ORDER BY row_number ASC
            LIMIT $3::int OFFSET $4::int
            """,
            organization_id,
            job_id,
            page_size,
            offset,
        )

        items = [
            {
                "row_number": int(r["row_number"]),
                "error_code": r.get("error_code"),
                "error_message": r.get("error_message"),
                "raw_row": r.get("raw_row"),
                "updated_at": r.get("updated_at").isoformat() if r.get("updated_at") else None,
            }
            for r in fetched
        ]
        return items, int(total or 0)
