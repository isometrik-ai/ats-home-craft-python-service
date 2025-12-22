"""Organisation Member Repository Module - AsyncPG Implementation.

This repository encapsulates all DB operations for organization_members.
"""

from typing import Any

import asyncpg

from apps.user_service.app.dependencies.logger import get_logger

logger = get_logger("organisation_member_repository")


class OrganisationMemberRepository:
    """Database operations class for organisation members using asyncpg."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        self.db_connection = db_connection

    async def add_member(
        self,
        organization_id: str,
        member_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Add a member to an organisation."""
        query = """
            INSERT INTO organization_members (
                user_id,
                isometrik_user_id,
                email,
                role_id,
                role,
                status,
                organization_id,
                created_at,
                updated_at,
                joined_at,
                first_name,
                last_name,
                phone,
                timezone,
                salutation,
                invited_by
            )
            VALUES (
                $1, $2, $3, $4, $5, COALESCE($6, 'active'),
                $7, NOW(), NOW(), NOW(),
                $8, $9, $10, COALESCE($11, 'UTC'), $12, $13
            )
            RETURNING *
        """
        row = await self.db_connection.fetchrow(
            query,
            member_data.get("user_id"),
            member_data.get("isometrik_user_id"),
            member_data.get("email"),
            member_data.get("role_id"),
            member_data.get("role"),
            member_data.get("status"),
            organization_id,
            member_data.get("first_name"),
            member_data.get("last_name"),
            member_data.get("phone"),
            member_data.get("timezone"),
            member_data.get("salutation"),
            member_data.get("invited_by"),
        )
        return dict(row) if row else {}
