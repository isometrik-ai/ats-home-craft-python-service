"""Invite Database Repository Module - AsyncPG Implementation

This module contains invite-related database operations using asyncpg.
All SQL queries for invitation management are centralized here with proper
transaction handling and efficient batch operations.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from apps.user_service.app.schemas.enums import InviteStatus, OrganizationMemberStatus
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("invite_repository")

_PATCH_PENDING_INVITE_RESULT_META = frozenset({"invite_ok", "role_ok", "previous_role_id"})


@dataclass(frozen=True)
class PatchPendingInviteResult:
    """Outcome of :meth:`InviteRepository.patch_pending_invitation` (one DB round-trip).

    ``invite_ok`` — invitation exists, belongs to the org, and is in the given pending status.
    ``role_ok`` — role exists for that org.

    When both are true, ``updated_row`` is the post-update ``organization_invites`` row and
    ``previous_role_id`` is the role UUID before the update (for audit diffs).
    """

    updated_row: dict[str, Any] | None
    invite_ok: bool
    role_ok: bool
    previous_role_id: str | None = None


class InviteRepository:
    """Database operations class for invitation management using asyncpg."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    # CREATE OPERATIONS
    async def create_invite(self, invite_data: dict[str, Any]) -> dict[str, Any]:
        """Create a new organization invitation."""
        query = """
            INSERT INTO organization_invites (
                organization_id,
                email,
                role_id,
                token_hash,
                invited_by,
                status,
                expires_at,
                metadata,
                created_at,
                updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, NOW(), NOW())
            RETURNING *
        """
        row = await self.db_connection.fetchrow(
            query,
            invite_data["organization_id"],
            invite_data["email"],
            invite_data["role_id"],
            invite_data["token_hash"],
            invite_data["invited_by"],
            invite_data.get("status", InviteStatus.PENDING.value),
            invite_data["expires_at"],
            json.dumps(invite_data.get("metadata", {})),
        )
        return dict(row) if row else {}

    # READ OPERATIONS
    def _get_invite_select_clause(self) -> str:
        """Get the common SELECT clause for invite queries with organization join.

        Returns:
            str: SQL SELECT clause string
        """
        return """
            SELECT
                i.id,
                i.organization_id,
                i.email,
                i.role_id,
                i.token_hash,
                i.invited_by,
                i.status,
                i.expires_at,
                i.created_at,
                i.updated_at,
                i.metadata,
                json_build_object(
                    'name', o.name,
                    'slug', o.slug,
                    'domain', o.domain
                ) as organizations
            FROM organization_invites i
            LEFT JOIN organizations o ON o.id = i.organization_id
        """

    async def get_invite_by_token(
        self, token_hash: str, for_update: bool = False
    ) -> dict[str, Any] | None:
        """Get invitation details by token hash.

        Args:
            token_hash: The hashed token to search for
            for_update: If True, locks the row with SELECT FOR UPDATE for atomic operations
        """
        # Use FOR UPDATE OF i to only lock the organization_invites table,
        # not the joined organizations table (which is on the nullable side of LEFT JOIN)
        for_update_clause = "FOR UPDATE OF i" if for_update else ""
        query = f"""
            {self._get_invite_select_clause()}
            WHERE i.token_hash = $1
            LIMIT 1
            {for_update_clause}
        """
        row = await self.db_connection.fetchrow(query, token_hash)
        return dict(row) if row else None

    async def get_invite_by_id(self, invite_id: str) -> dict[str, Any] | None:
        """Get invitation details by ID."""
        query = f"""
            {self._get_invite_select_clause()}
            WHERE i.id = $1
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, invite_id)
        return dict(row) if row else None

    async def get_organization_invites(
        self,
        organization_id: str,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get all invitations for an organization with optional filtering."""
        query = """
            SELECT
                id,
                organization_id,
                email,
                role_id,
                invited_by,
                status,
                expires_at,
                created_at,
                updated_at,
                metadata
            FROM organization_invites
            WHERE organization_id = $1
                AND ($2::text IS NULL OR status = $2)
            ORDER BY created_at DESC
            LIMIT $3
            OFFSET $4
        """
        rows = await self.db_connection.fetch(query, organization_id, status, limit, offset)
        return [dict(row) for row in rows]

    async def get_organization_invites_count(
        self, organization_id: str, status: str | None = None
    ) -> int:
        """Get count of invitations for an organization."""
        query = """
            SELECT COUNT(*)::int
            FROM organization_invites
            WHERE organization_id = $1
                AND ($2::text IS NULL OR status = $2)
        """
        return await self.db_connection.fetchval(query, organization_id, status) or 0

    # VALIDATION OPERATIONS
    async def check_existing_invite(
        self, organization_id: str, email: str, status: str | None = None
    ) -> dict[str, Any] | None:
        """Check if an invitation already exists for the email and organization."""
        conditions = ["organization_id = $1", "email = $2"]
        params: list[Any] = [organization_id, email]

        if status:
            conditions.append("status = $3")
            params.append(status)

        where_clause = " AND ".join(conditions)

        query = f"""
            SELECT *
            FROM organization_invites
            WHERE {where_clause}
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, *params)
        return dict(row) if row else None

    async def check_user_membership(self, organization_id: str, email: str) -> bool:
        """Check if user is already a member of the organization."""
        deleted_status = OrganizationMemberStatus.DELETED.value
        query = """
            SELECT EXISTS(
                SELECT 1
                FROM organization_members
                WHERE organization_id = $1 AND email = $2 AND status != $3
            )
        """
        exists = await self.db_connection.fetchval(query, organization_id, email, deleted_status)
        return bool(exists)

    # UPDATE OPERATIONS
    async def update_invite_status(
        self, invite_id: str, status: str, accepted_by: str | None = None
    ) -> None:
        """Update invitation status."""
        query = """
            UPDATE organization_invites
            SET status = $1,
                accepted_by = COALESCE($2, accepted_by),
                accepted_at = CASE WHEN $2 IS NOT NULL THEN NOW() ELSE accepted_at END,
                updated_at = NOW()
            WHERE id = $3
        """
        result = await self.db_connection.execute(query, status, accepted_by, invite_id)

        if result == "UPDATE 0":
            raise NotFoundException(
                message_key="invitations.errors.invitation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def update_invite_expiration(
        self, invite_id: str, expires_at: datetime
    ) -> dict[str, Any] | None:
        """Update invitation expiration date."""
        query = """
            UPDATE organization_invites
            SET expires_at = $1,
                updated_at = NOW()
            WHERE id = $2
            RETURNING *
        """
        row = await self.db_connection.fetchrow(query, expires_at, invite_id)
        return dict(row) if row else None

    async def update_invite_token_and_expiration(
        self, invite_id: str, token_hash: str, expires_at: datetime
    ) -> dict[str, Any] | None:
        """Update invitation token hash and expiration date."""
        query = """
            UPDATE organization_invites
            SET token_hash = $1,
                expires_at = $2,
                updated_at = NOW()
            WHERE id = $3
            RETURNING *
        """
        row = await self.db_connection.fetchrow(query, token_hash, expires_at, invite_id)
        return dict(row) if row else None

    async def renew_expired_invite(
        self, invite_id: str, invite_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Refresh an expired pending invitation with a new token, role, and metadata."""
        query = """
            UPDATE organization_invites
            SET role_id = $1,
                token_hash = $2,
                invited_by = $3,
                expires_at = $4,
                metadata = $5::jsonb,
                updated_at = NOW()
            WHERE id = $6
              AND status = $7
              AND expires_at <= NOW()
            RETURNING *
        """
        row = await self.db_connection.fetchrow(
            query,
            invite_data["role_id"],
            invite_data["token_hash"],
            invite_data["invited_by"],
            invite_data["expires_at"],
            json.dumps(invite_data.get("metadata", {})),
            invite_id,
            InviteStatus.PENDING.value,
        )
        return dict(row) if row else None

    async def patch_pending_invitation(
        self,
        invite_id: str,
        organization_id: str,
        pending_status: str,
        *,
        role_id: str,
    ) -> PatchPendingInviteResult:
        """Validate pending invite + role and update ``role_id`` in **one** statement.

        Steps (single SQL, atomic):

        1. ``invite_ok`` — row in ``organization_invites`` matches id, org, and pending status.
        2. ``role_ok`` — row in ``roles`` matches id and org.
        3. ``UPDATE`` runs only when both preconditions hold (join via CTEs); otherwise no write.

        The outer ``SELECT`` returns the two flags plus the updated row (or nulls) so callers
        can return precise errors without a second query.
        """
        query = """
            WITH invite_check AS (
                SELECT id, role_id AS previous_role_id
                FROM organization_invites
                WHERE id = $1::uuid
                  AND organization_id = $2::uuid
                  AND status = $3
            ),
            role_check AS (
                SELECT id
                FROM roles
                WHERE id = $4::uuid
                  AND organization_id = $2::uuid
            ),
            updated AS (
                UPDATE organization_invites AS oi
                SET
                    role_id = $4::uuid,
                    updated_at = CASE
                        WHEN $4::uuid IS DISTINCT FROM oi.role_id THEN NOW()
                        ELSE oi.updated_at
                    END
                FROM invite_check AS ic
                CROSS JOIN role_check AS rc
                WHERE oi.id = ic.id
                RETURNING oi.*, ic.previous_role_id
            )
            SELECT
                EXISTS (SELECT 1 FROM invite_check) AS invite_ok,
                EXISTS (SELECT 1 FROM role_check) AS role_ok,
                u.id,
                u.organization_id,
                u.email,
                u.role_id,
                u.token_hash,
                u.invited_by,
                u.status,
                u.expires_at,
                u.created_at,
                u.updated_at,
                u.metadata,
                u.previous_role_id
            FROM (SELECT 1) AS _driver
            LEFT JOIN LATERAL (SELECT * FROM updated LIMIT 1) AS u ON TRUE
        """
        row = await self.db_connection.fetchrow(
            query,
            invite_id,
            organization_id,
            pending_status,
            role_id,
        )
        if not row:
            return PatchPendingInviteResult(updated_row=None, invite_ok=False, role_ok=False)

        invite_ok = bool(row["invite_ok"])
        role_ok = bool(row["role_ok"])
        if row["id"] is None:
            return PatchPendingInviteResult(updated_row=None, invite_ok=invite_ok, role_ok=role_ok)

        prev = row["previous_role_id"]
        prev_str = str(prev) if prev is not None else None
        data = {k: row[k] for k in row.keys() if k not in _PATCH_PENDING_INVITE_RESULT_META}
        return PatchPendingInviteResult(
            updated_row=data,
            invite_ok=invite_ok,
            role_ok=role_ok,
            previous_role_id=prev_str,
        )

    # DELETE OPERATIONS
    async def delete_invite(self, invite_id: str, organization_id: str) -> None:
        """Delete an invitation."""
        query = """
            DELETE FROM organization_invites
            WHERE id = $1 AND organization_id = $2
            RETURNING id
        """
        result = await self.db_connection.fetchval(query, invite_id, organization_id)
        if result is None:
            raise NotFoundException(
                message_key="invitations.errors.invitation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
