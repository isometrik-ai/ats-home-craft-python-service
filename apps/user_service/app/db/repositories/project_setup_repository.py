"""Project setup steps persistence and step-gating helpers."""

from __future__ import annotations

import json
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ProjectSetupStep, SetupStepStatus

PROJECT_SETUP_STEP_KEYS: tuple[str, ...] = tuple(step.value for step in ProjectSetupStep)


class ProjectSetupRepository(BaseRepository):
    """Database operations for public.project_setup_steps."""

    async def ensure_steps(
        self,
        *,
        organization_id: str,
        project_id: str,
        step_keys: list[str],
    ) -> None:
        """Insert missing wizard steps (not_started) for a project."""
        if not step_keys:
            return
        await self.db_connection.execute(
            """
            INSERT INTO project_setup_steps (
                organization_id, project_id, step_key, status
            )
            SELECT $1::uuid, $2::uuid, step_key, $3::setup_step_status
            FROM unnest($4::project_setup_step[]) AS step_key
            ON CONFLICT (project_id, step_key) DO NOTHING
            """,
            organization_id,
            project_id,
            SetupStepStatus.NOT_STARTED.value,
            step_keys,
        )

    async def skip_steps(
        self,
        *,
        organization_id: str,
        project_id: str,
        step_keys: list[str],
    ) -> None:
        """Upsert the given steps as skipped (used for excluded property types)."""
        if not step_keys:
            return
        await self.db_connection.execute(
            """
            INSERT INTO project_setup_steps (
                organization_id, project_id, step_key, status, completed_at
            )
            SELECT $1::uuid, $2::uuid, step_key, $3::setup_step_status, now()
            FROM unnest($4::project_setup_step[]) AS step_key
            ON CONFLICT (project_id, step_key) DO UPDATE
              SET status = $3::setup_step_status,
                  completed_at = COALESCE(project_setup_steps.completed_at, now()),
                  updated_at = now()
            WHERE project_setup_steps.status <> 'completed'::setup_step_status
            """,
            organization_id,
            project_id,
            SetupStepStatus.SKIPPED.value,
            step_keys,
        )

    async def set_step_status(
        self,
        *,
        organization_id: str,
        project_id: str,
        step_key: str,
        status: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Upsert a single step's status (and optional data payload)."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO project_setup_steps (
                organization_id, project_id, step_key, status, data, completed_at
            )
            VALUES (
                $1::uuid, $2::uuid, $3::project_setup_step, $4::setup_step_status,
                COALESCE($5::jsonb, '{}'::jsonb),
                CASE WHEN $4 IN ('completed', 'skipped') THEN now() ELSE NULL END
            )
            ON CONFLICT (project_id, step_key) DO UPDATE
              SET status = $4::setup_step_status,
                  data = CASE
                      WHEN $5 IS NULL THEN project_setup_steps.data
                      ELSE $5::jsonb
                  END,
                  completed_at = CASE
                      WHEN $4 IN ('completed', 'skipped')
                          THEN COALESCE(project_setup_steps.completed_at, now())
                      ELSE project_setup_steps.completed_at
                  END,
                  updated_at = now()
            RETURNING step_key::text AS step_key, status::text AS status,
                      completed_at, updated_at
            """,
            organization_id,
            project_id,
            step_key,
            status,
            json.dumps(data) if data is not None else None,
        )
        return dict(row) if row else None

    async def list_steps(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """List steps ordered by canonical wizard order."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              pss.step_key::text AS step_key,
              pss.status::text AS status,
              pss.completed_at,
              pss.updated_at
            FROM project_setup_steps pss
            WHERE pss.organization_id = $1::uuid
              AND pss.project_id = $2::uuid
            ORDER BY array_position($3::text[], pss.step_key::text)
            """,
            organization_id,
            project_id,
            list(PROJECT_SETUP_STEP_KEYS),
        )
        return [dict(row) for row in rows]

    async def get_step(
        self,
        *,
        organization_id: str,
        project_id: str,
        step_key: str,
    ) -> dict[str, Any] | None:
        """Fetch a single setup step row."""
        row = await self.db_connection.fetchrow(
            """
            SELECT step_key::text AS step_key, status::text AS status,
                   completed_at, updated_at
            FROM project_setup_steps
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
              AND step_key = $3::project_setup_step
            """,
            organization_id,
            project_id,
            step_key,
        )
        return dict(row) if row else None

    async def is_completed(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> bool:
        """True when all steps are completed or skipped."""
        row = await self.db_connection.fetchval(
            """
            SELECT COUNT(*) > 0
                AND COUNT(*) = COUNT(*) FILTER (
                    WHERE status IN ('completed', 'skipped')
                )
            FROM project_setup_steps
            WHERE organization_id = $1::uuid
              AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return bool(row)
