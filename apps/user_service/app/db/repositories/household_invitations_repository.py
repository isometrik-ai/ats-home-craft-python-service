"""Household invitation persistence."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import HouseholdInvitationStatus


class HouseholdInvitationsRepository(BaseRepository):
    """Database operations for public.household_invitations."""

    async def insert_invitation(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a household invitation row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO household_invitations (
                organization_id, contact_id, contact_unit_id,
                invited_by_contact_id, phone_isd_code, phone_number,
                token, token_hash, status, expires_at
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8,
                $9::household_invitation_status, $10
            )
            RETURNING *
            """,
            data["organization_id"],
            data["contact_id"],
            data["contact_unit_id"],
            data["invited_by_contact_id"],
            data["phone_isd_code"],
            data["phone_number"],
            data["token"],
            data["token_hash"],
            data.get("status", HouseholdInvitationStatus.PENDING.value),
            data["expires_at"],
        )
        return dict(row)

    async def get_by_token_hash(
        self,
        token_hash: str,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch invitation by token hash."""
        lock = "FOR UPDATE" if for_update else ""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT *
            FROM household_invitations
            WHERE token_hash = $1
            {lock}
            LIMIT 1
            """,
            token_hash,
        )
        return dict(row) if row else None

    async def get_pending_by_contact_unit(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any] | None:
        """Return a pending invitation for a contact_unit link."""
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM household_invitations
            WHERE organization_id = $1::uuid
              AND contact_unit_id = $2::uuid
              AND status = $3::household_invitation_status
            LIMIT 1
            """,
            organization_id,
            contact_unit_id,
            HouseholdInvitationStatus.PENDING.value,
        )
        return dict(row) if row else None

    async def get_by_contact_unit(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
    ) -> dict[str, Any] | None:
        """Return the invitation row for a contact_unit link (one row per link)."""
        row = await self.db_connection.fetchrow(
            """
            SELECT *
            FROM household_invitations
            WHERE organization_id = $1::uuid
              AND contact_unit_id = $2::uuid
            LIMIT 1
            """,
            organization_id,
            contact_unit_id,
        )
        return dict(row) if row else None

    async def reactivate_invitation(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
        invited_by_contact_id: str,
        phone_isd_code: str,
        phone_number: str,
        token: str,
        token_hash: str,
        expires_at: Any,
    ) -> dict[str, Any] | None:
        """Re-open a cancelled/expired/declined invitation as pending."""
        reactivatable = [
            HouseholdInvitationStatus.CANCELLED.value,
            HouseholdInvitationStatus.EXPIRED.value,
            HouseholdInvitationStatus.DECLINED.value,
        ]
        row = await self.db_connection.fetchrow(
            """
            UPDATE household_invitations
            SET status = $6::household_invitation_status,
                invited_by_contact_id = $3::uuid,
                phone_isd_code = $4,
                phone_number = $5,
                token = $7,
                token_hash = $8,
                expires_at = $9,
                accepted_at = NULL,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND contact_unit_id = $2::uuid
              AND status = ANY($10::household_invitation_status[])
            RETURNING *
            """,
            organization_id,
            contact_unit_id,
            invited_by_contact_id,
            phone_isd_code,
            phone_number,
            HouseholdInvitationStatus.PENDING.value,
            token,
            token_hash,
            expires_at,
            reactivatable,
        )
        return dict(row) if row else None

    async def cancel_by_contact_unit(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
    ) -> bool:
        """Cancel a pending invitation for a contact_unit link."""
        result = await self.db_connection.execute(
            """
            UPDATE household_invitations
            SET status = $4::household_invitation_status,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND contact_unit_id = $2::uuid
              AND status = $3::household_invitation_status
            """,
            organization_id,
            contact_unit_id,
            HouseholdInvitationStatus.PENDING.value,
            HouseholdInvitationStatus.CANCELLED.value,
        )
        return result.upper().startswith("UPDATE") and not result.endswith(" 0")

    async def renew_invitation(
        self,
        *,
        invitation_id: str,
        token: str,
        token_hash: str,
        expires_at: Any,
    ) -> dict[str, Any] | None:
        """Refresh token and expiry for a pending invitation."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE household_invitations
            SET token = $2,
                token_hash = $3,
                expires_at = $4,
                updated_at = now()
            WHERE id = $1::uuid
              AND status = $5::household_invitation_status
            RETURNING *
            """,
            invitation_id,
            token,
            token_hash,
            expires_at,
            HouseholdInvitationStatus.PENDING.value,
        )
        return dict(row) if row else None

    async def update_pending_phone(
        self,
        *,
        organization_id: str,
        contact_unit_id: str,
        phone_isd_code: str,
        phone_number: str,
    ) -> dict[str, Any] | None:
        """Update phone on a pending household invitation."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE household_invitations
            SET phone_isd_code = $3,
                phone_number = $4,
                updated_at = now()
            WHERE organization_id = $1::uuid
              AND contact_unit_id = $2::uuid
              AND status = $5::household_invitation_status
            RETURNING id::text AS id
            """,
            organization_id,
            contact_unit_id,
            phone_isd_code,
            phone_number,
            HouseholdInvitationStatus.PENDING.value,
        )
        return dict(row) if row else None

    async def mark_accepted(
        self,
        *,
        invitation_id: str,
    ) -> dict[str, Any] | None:
        """Mark invitation accepted."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE household_invitations
            SET status = $2::household_invitation_status,
                accepted_at = now(),
                updated_at = now()
            WHERE id = $1::uuid
            RETURNING *
            """,
            invitation_id,
            HouseholdInvitationStatus.ACCEPTED.value,
        )
        return dict(row) if row else None

    async def mark_declined(
        self,
        *,
        invitation_id: str,
    ) -> dict[str, Any] | None:
        """Mark a pending invitation declined by the invitee."""
        row = await self.db_connection.fetchrow(
            """
            UPDATE household_invitations
            SET status = $2::household_invitation_status,
                updated_at = now()
            WHERE id = $1::uuid
              AND status = $3::household_invitation_status
            RETURNING *
            """,
            invitation_id,
            HouseholdInvitationStatus.DECLINED.value,
            HouseholdInvitationStatus.PENDING.value,
        )
        return dict(row) if row else None
