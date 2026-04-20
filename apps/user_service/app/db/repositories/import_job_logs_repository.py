"""Import job logs repository (one log row per job)."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.utils.common_utils import (
    parse_json_any,
    serialize_jsonb_param,
)


class ImportJobLogsRepository(BaseRepository):
    """Repository for `public.import_job_logs` (org-scoped, unique per job)."""

    TABLE = "import_job_logs"

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        super().__init__(db_connection=db_connection)

    async def list_logs(
        self,
        *,
        organization_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List job logs for an organization (paginated; newest first).

        Note: logs are stored against the internal job UUID (`import_jobs.id`),
        but API consumers typically want the public `job_key`, so we join.
        """
        page = max(int(page or 1), 1)
        page_size = max(int(page_size or 1), 1)
        offset = (page - 1) * page_size

        fetched = await self.db_connection.fetch(
            """
            SELECT
              COUNT(*) OVER()::int AS total,
              j.job_key AS job_id,
              j.status AS job_status,
              l.payload,
              l.created_at,
              l.updated_at
            FROM import_job_logs l
            JOIN import_jobs j ON j.id = l.job_id
            WHERE l.organization_id = $1::uuid
              AND j.organization_id = $1::uuid
              AND j.import_type = 'contacts'
            ORDER BY l.updated_at DESC
            LIMIT $2::int OFFSET $3::int
            """,
            organization_id,
            page_size,
            offset,
        )

        total = int(fetched[0]["total"]) if fetched else 0

        items = []
        for row in fetched:
            row_dict = dict(row)
            items.append(
                {
                    "job_id": str(row_dict.get("job_id") or ""),
                    "job_status": str(row_dict.get("job_status") or ""),
                    "payload": parse_json_any(row_dict.get("payload"), default={}) or {},
                    "created_at": row_dict.get("created_at").isoformat()
                    if row_dict.get("created_at")
                    else None,
                    "updated_at": row_dict.get("updated_at").isoformat()
                    if row_dict.get("updated_at")
                    else None,
                }
            )

        return items, total

    async def upsert_payload(
        self,
        *,
        organization_id: str,
        job_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Insert or update the latest job log payload (idempotent by job_id)."""
        await self.db_connection.execute(
            """
            INSERT INTO import_job_logs (organization_id, job_id, payload)
            VALUES ($1::uuid, $2::uuid, $3::jsonb)
            ON CONFLICT (job_id)
            DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
            """,
            organization_id,
            job_id,
            serialize_jsonb_param("payload", payload, frozenset({"payload"})),
        )
