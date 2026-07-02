"""Contact onboarding steps persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ContactOnboardingStep, SetupStepStatus

ONBOARDING_STEP_KEYS: tuple[str, ...] = tuple(step.value for step in ContactOnboardingStep)


class ContactOnboardingRepository(BaseRepository):
    """Database operations for public.contact_onboarding_steps."""

    async def ensure_steps(self, *, organization_id: str, contact_id: str) -> None:
        """Insert missing wizard steps for a contact."""
        await self.db_connection.execute(
            """
            INSERT INTO contact_onboarding_steps (
                organization_id, contact_id, step_key, status
            )
            SELECT $1::uuid, $2::uuid, step_key, $3
            FROM unnest($4::contact_onboarding_step[]) AS step_key
            ON CONFLICT (contact_id, step_key) DO NOTHING
            """,
            organization_id,
            contact_id,
            SetupStepStatus.NOT_STARTED.value,
            list(ONBOARDING_STEP_KEYS),
        )

    async def list_steps(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> list[dict[str, Any]]:
        """List steps ordered by canonical wizard order."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              cos.step_key::text AS step_key,
              cos.status::text AS status,
              cos.completed_at,
              cos.updated_at
            FROM contact_onboarding_steps cos
            WHERE cos.organization_id = $1::uuid
              AND cos.contact_id = $2::uuid
            ORDER BY array_position($3::text[], cos.step_key::text)
            """,
            organization_id,
            contact_id,
            list(ONBOARDING_STEP_KEYS),
        )
        return [dict(row) for row in rows]

    async def complete_step(
        self,
        *,
        organization_id: str,
        contact_id: str,
        step_key: str,
    ) -> dict[str, Any] | None:
        """Mark a step completed."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE contact_onboarding_steps
            SET status = $4,
                completed_at = COALESCE(completed_at, now()),
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND step_key = $3::contact_onboarding_step
            RETURNING step_key::text AS step_key, status::text AS status, completed_at
            """,
            organization_id,
            contact_id,
            step_key,
            SetupStepStatus.COMPLETED.value,
        )
        return dict(row) if row else None

    async def skip_step(
        self,
        *,
        organization_id: str,
        contact_id: str,
        step_key: str,
    ) -> dict[str, Any] | None:
        """Mark a step skipped."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE contact_onboarding_steps
            SET status = $4,
                completed_at = COALESCE(completed_at, now()),
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND step_key = $3::contact_onboarding_step
            RETURNING step_key::text AS step_key, status::text AS status, completed_at
            """,
            organization_id,
            contact_id,
            step_key,
            SetupStepStatus.SKIPPED.value,
        )
        return dict(row) if row else None

    async def is_wizard_completed(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> bool:
        """True when all steps are completed or skipped."""
        row = await self.db_connection.fetchval(
            """
            SELECT COUNT(*) = COUNT(*) FILTER (
                WHERE status IN ('completed', 'skipped')
            )
            FROM contact_onboarding_steps
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
            """,
            organization_id,
            contact_id,
        )
        return bool(row)
