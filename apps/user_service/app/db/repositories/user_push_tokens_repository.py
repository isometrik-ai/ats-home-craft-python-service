"""User push token persistence (public.user_push_tokens) — asyncpg."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.base_repository import BaseRepository


class UserPushTokensRepository(BaseRepository):
    """Database operations for user push token registrations."""

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection."""
        super().__init__(db_connection=db_connection)

    async def upsert_device(
        self,
        *,
        device_id: str,
        organization_id: str,
        user_id: str,
        push_token: str,
        platform: str,
        provider: str = "fcm",
        app_version: str | None = None,
    ) -> dict[str, Any] | None:
        """Insert or reassign a device registration keyed by device_id."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO user_push_tokens (
              device_id,
              organization_id,
              user_id,
              push_token,
              platform,
              provider,
              app_version,
              last_seen_at,
              updated_at
            )
            VALUES (
              $1::text,
              $2::uuid,
              $3::uuid,
              $4::text,
              $5::text,
              $6::text,
              $7::text,
              NOW(),
              NOW()
            )
            ON CONFLICT (device_id) DO UPDATE SET
              organization_id = EXCLUDED.organization_id,
              user_id           = EXCLUDED.user_id,
              push_token        = EXCLUDED.push_token,
              platform          = EXCLUDED.platform,
              provider          = EXCLUDED.provider,
              app_version       = EXCLUDED.app_version,
              last_seen_at      = NOW(),
              updated_at        = NOW()
            RETURNING *
            """,
            device_id,
            organization_id,
            user_id,
            push_token,
            platform,
            provider,
            app_version,
        )
        return dict(row) if row else None

    async def delete_by_device_and_user(
        self,
        *,
        device_id: str,
        user_id: str,
    ) -> bool:
        """Delete a device registration scoped to the authenticated user."""
        row = await self.db_connection.fetchrow(
            """
            DELETE FROM user_push_tokens
            WHERE device_id = $1::text
              AND user_id = $2::uuid
            RETURNING id
            """,
            device_id,
            user_id,
        )
        return row is not None

    async def list_push_tokens_for_user(
        self,
        *,
        organization_id: str,
        user_id: str,
    ) -> list[str]:
        """Return distinct non-empty FCM tokens registered for an org-scoped user."""
        if not organization_id or not user_id:
            return []
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT push_token
            FROM user_push_tokens
            WHERE organization_id = $1::uuid
              AND user_id = $2::uuid
              AND push_token IS NOT NULL
              AND btrim(push_token) <> ''
            ORDER BY push_token
            """,
            organization_id,
            user_id,
        )
        return [str(row["push_token"]) for row in rows if row.get("push_token")]

    async def delete_by_user(
        self,
        *,
        user_id: str,
    ) -> int:
        """Delete all device registrations for a user."""
        result = await self.db_connection.execute(
            """
            DELETE FROM user_push_tokens
            WHERE user_id = $1::uuid
            """,
            user_id,
        )
        try:
            return int(str(result).split()[-1])
        except (ValueError, IndexError):
            return 0
