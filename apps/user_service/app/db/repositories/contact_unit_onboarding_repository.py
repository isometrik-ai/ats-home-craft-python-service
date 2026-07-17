"""Per-unit contact onboarding step persistence (vehicles, household)."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import ContactOnboardingStep, SetupStepStatus

UNIT_ONBOARDING_STEP_KEYS: tuple[str, ...] = (
    ContactOnboardingStep.VEHICLES.value,
    ContactOnboardingStep.HOUSEHOLD.value,
)


class ContactUnitOnboardingRepository(BaseRepository):
    """Database operations for public.contact_unit_onboarding_steps."""

    async def ensure_steps(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_id: str,
    ) -> None:
        """Insert missing unit-level wizard steps."""
        await self.db_connection.execute(
            """
            INSERT INTO contact_unit_onboarding_steps (
                organization_id, contact_id, contact_unit_id, step_key, status
            )
            SELECT $1::uuid, $2::uuid, $3::uuid, step_key, $4
            FROM unnest($5::contact_onboarding_step[]) AS step_key
            ON CONFLICT (contact_unit_id, step_key) DO NOTHING
            """,
            organization_id,
            contact_id,
            contact_unit_id,
            SetupStepStatus.NOT_STARTED.value,
            list(UNIT_ONBOARDING_STEP_KEYS),
        )

    async def ensure_steps_for_units(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_ids: list[str],
    ) -> None:
        """Ensure unit steps exist for each contact_unit row."""
        for contact_unit_id in contact_unit_ids:
            await self.ensure_steps(
                organization_id=organization_id,
                contact_id=contact_id,
                contact_unit_id=contact_unit_id,
            )

    async def list_steps_for_contact(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> list[dict[str, Any]]:
        """List all unit-level steps for a contact, ordered by unit then step."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              cuos.contact_unit_id::text AS contact_unit_id,
              cuos.contact_id::text AS contact_id,
              cu.unit_id::text AS unit_id,
              u.code AS unit_code,
              cuos.step_key::text AS step_key,
              cuos.status::text AS status,
              cuos.completed_at,
              cuos.updated_at
            FROM contact_unit_onboarding_steps cuos
            INNER JOIN contact_units cu ON cu.id = cuos.contact_unit_id
            INNER JOIN units u ON u.id = cu.unit_id
            WHERE cuos.organization_id = $1::uuid
              AND cuos.contact_id = $2::uuid
              AND cu.status = 'active'
            ORDER BY cu.sort_order, cu.created_at, array_position($3::text[], cuos.step_key::text)
            """,
            organization_id,
            contact_id,
            list(UNIT_ONBOARDING_STEP_KEYS),
        )
        return [dict(row) for row in rows]

    async def list_steps_for_unit(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_id: str,
    ) -> list[dict[str, Any]]:
        """List unit-level steps for one contact_unit."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              contact_unit_id::text AS contact_unit_id,
              contact_id::text AS contact_id,
              step_key::text AS step_key,
              status::text AS status,
              completed_at,
              updated_at
            FROM contact_unit_onboarding_steps
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND contact_unit_id = $3::uuid
            ORDER BY array_position($4::text[], step_key::text)
            """,
            organization_id,
            contact_id,
            contact_unit_id,
            list(UNIT_ONBOARDING_STEP_KEYS),
        )
        return [dict(row) for row in rows]

    async def complete_step(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_id: str,
        step_key: str,
    ) -> dict[str, Any] | None:
        """Mark a unit-level step completed."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE contact_unit_onboarding_steps
            SET status = $5,
                completed_at = COALESCE(completed_at, now()),
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND contact_unit_id = $3::uuid
              AND step_key = $4::contact_onboarding_step
            RETURNING step_key::text AS step_key, status::text AS status, completed_at
            """,
            organization_id,
            contact_id,
            contact_unit_id,
            step_key,
            SetupStepStatus.COMPLETED.value,
        )
        return dict(row) if row else None

    async def skip_step(
        self,
        *,
        organization_id: str,
        contact_id: str,
        contact_unit_id: str,
        step_key: str,
    ) -> dict[str, Any] | None:
        """Mark a unit-level step skipped."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE contact_unit_onboarding_steps
            SET status = $5,
                completed_at = COALESCE(completed_at, now()),
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND contact_unit_id = $3::uuid
              AND step_key = $4::contact_onboarding_step
            RETURNING step_key::text AS step_key, status::text AS status, completed_at
            """,
            organization_id,
            contact_id,
            contact_unit_id,
            step_key,
            SetupStepStatus.SKIPPED.value,
        )
        return dict(row) if row else None

    async def all_unit_steps_terminal(
        self,
        *,
        organization_id: str,
        contact_id: str,
    ) -> bool:
        """True when every active unit has vehicles + household completed or skipped."""
        row = await self.db_connection.fetchval(
            """
            WITH active_units AS (
                SELECT cu.id
                FROM contact_units cu
                WHERE cu.organization_id = $1::uuid
                  AND cu.contact_id = $2::uuid
                  AND cu.status = 'active'
            ),
            expected AS (
                SELECT au.id AS contact_unit_id, step_key
                FROM active_units au
                CROSS JOIN unnest($3::contact_onboarding_step[]) AS step_key
            )
            SELECT COUNT(*) = COUNT(*) FILTER (
                WHERE cuos.status IN ('completed', 'skipped')
            )
            FROM expected e
            LEFT JOIN contact_unit_onboarding_steps cuos
              ON cuos.contact_unit_id = e.contact_unit_id
             AND cuos.step_key = e.step_key
            """,
            organization_id,
            contact_id,
            list(UNIT_ONBOARDING_STEP_KEYS),
        )
        return bool(row)
