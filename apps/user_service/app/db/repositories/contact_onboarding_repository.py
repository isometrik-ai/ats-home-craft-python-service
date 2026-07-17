"""Contact onboarding steps persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ContactOnboardingStep, SetupStepStatus

CONTACT_LEVEL_STEP_KEYS: tuple[str, ...] = (
    ContactOnboardingStep.COMPLETE_PROFILE.value,
    ContactOnboardingStep.SELECT_PROPERTIES.value,
    ContactOnboardingStep.CHOOSE_UNIT.value,
    ContactOnboardingStep.REVIEW.value,
)

FAMILY_ONBOARDING_STEP_KEYS: tuple[str, ...] = (ContactOnboardingStep.COMPLETE_PROFILE.value,)

# Full wizard order for documentation / legacy references.
ONBOARDING_STEP_KEYS: tuple[str, ...] = (
    ContactOnboardingStep.COMPLETE_PROFILE.value,
    ContactOnboardingStep.SELECT_PROPERTIES.value,
    ContactOnboardingStep.VEHICLES.value,
    ContactOnboardingStep.HOUSEHOLD.value,
    ContactOnboardingStep.CHOOSE_UNIT.value,
    ContactOnboardingStep.REVIEW.value,
)


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
            list(CONTACT_LEVEL_STEP_KEYS),
        )

    async def ensure_profile_step(self, *, organization_id: str, contact_id: str) -> None:
        """Insert the profile step only (family / household members)."""
        await self.db_connection.execute(
            """
            INSERT INTO contact_onboarding_steps (
                organization_id, contact_id, step_key, status
            )
            VALUES ($1::uuid, $2::uuid, $3::contact_onboarding_step, $4)
            ON CONFLICT (contact_id, step_key) DO NOTHING
            """,
            organization_id,
            contact_id,
            ContactOnboardingStep.COMPLETE_PROFILE.value,
            SetupStepStatus.NOT_STARTED.value,
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
            list(CONTACT_LEVEL_STEP_KEYS),
        )
        return [dict(row) for row in rows]

    async def list_profile_step(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> list[dict[str, Any]]:
        """Return only the complete_profile step row for a contact."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              cos.step_key::text AS step_key,
              cos.status::text AS status,
              cos.completed_at,
              cos.updated_at
            FROM contact_onboarding_steps cos
            WHERE cos.organization_id = $1::uuid
              AND cos.contact_id = $2::uuid
              AND cos.step_key = $3::contact_onboarding_step
            LIMIT 1
            """,
            organization_id,
            contact_id,
            ContactOnboardingStep.COMPLETE_PROFILE.value,
        )
        if row:
            return [dict(row)]
        return [
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": SetupStepStatus.NOT_STARTED.value,
                "completed_at": None,
                "updated_at": None,
            }
        ]

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
        """True when all contact-level steps are completed or skipped."""
        row = await self.db_connection.fetchval(
            """
            WITH expected AS (
                SELECT unnest($3::contact_onboarding_step[]) AS step_key
            )
            SELECT COUNT(*) = COUNT(*) FILTER (
                WHERE cos.status IN ('completed', 'skipped')
            )
            FROM expected e
            LEFT JOIN contact_onboarding_steps cos
              ON cos.organization_id = $1::uuid
             AND cos.contact_id = $2::uuid
             AND cos.step_key = e.step_key
            """,
            organization_id,
            contact_id,
            list(CONTACT_LEVEL_STEP_KEYS),
        )
        return bool(row)
