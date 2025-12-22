"""Invite Database Repository Module - AsyncPG Implementation

This module contains invite-related database operations using asyncpg.
All SQL queries for invitation management are centralized here with proper
transaction handling and efficient batch operations.
"""

import json
from typing import Any

import asyncpg

from apps.user_service.app.dependencies.logger import get_logger
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("invite_repository")


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
            invite_data.get("status", "pending"),
            invite_data["expires_at"],
            json.dumps(invite_data.get("metadata", {})),
        )
        return dict(row) if row else {}

    # READ OPERATIONS
    async def get_invite_by_token(self, token_hash: str) -> dict[str, Any] | None:
        """Get invitation details by token hash."""
        query = """
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
            WHERE i.token_hash = $1
            LIMIT 1
        """
        row = await self.db_connection.fetchrow(query, token_hash)
        return dict(row) if row else None

    async def get_invite_by_id(self, invite_id: str) -> dict[str, Any] | None:
        """Get invitation details by ID."""
        query = """
            SELECT
                i.id,
                i.organization_id,
                i.email,
                i.role_id,
                i.invited_by,
                i.token_hash,
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
        query = """
            SELECT EXISTS(
                SELECT 1
                FROM organization_members
                WHERE organization_id = $1 AND email = $2
            )
        """
        exists = await self.db_connection.fetchval(query, organization_id, email)
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
