"""Import job logs repository (one log row per job)."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository


class ImportJobLogsRepository(BaseRepository):
    """Repository for `public.import_job_logs` (org-scoped, unique per job)."""

    TABLE = "import_job_logs"

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        super().__init__(db_connection=db_connection)

    async def upsert_payload(
        self,
        *,
        organization_id: str,
        job_id: str,
        payload: dict[str, Any],
    ) -> None:
        await self.db_connection.execute(
            """
            INSERT INTO import_job_logs (organization_id, job_id, payload)
            VALUES ($1::uuid, $2::uuid, $3::jsonb)
            ON CONFLICT (job_id)
            DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
            """,
            organization_id,
            job_id,
            payload,
        )

