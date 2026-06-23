"""Unit tests for session context cache helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.shared_utils.session_context_cache import (
    invalidate_session_context_cache,
    invalidate_user_sessions_cache,
    resolve_session_context,
    resolve_session_context_from_db,
    resolve_session_context_from_redis,
    revoke_org_member_sessions_everywhere,
    revoke_organization_sessions_everywhere,
    warm_session_context_cache,
)


@pytest.fixture(autouse=True)
def _cache_settings(monkeypatch):
    """Enable session context cache settings for each test."""
    settings = SimpleNamespace(
        session_ctx_cache_enabled=True,
        session_ctx_cache_ttl_seconds=300,
        session_revoked_cache_ttl_seconds=3600,
        user_deleted_cache_ttl_seconds=86400,
    )
    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache._settings",
        lambda: settings,
    )
    return settings


@pytest.mark.asyncio
async def test_redis_resolve_returns_cache_hit():
    """Redis hit returns context without querying the database."""
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(return_value=[0, 0, '{"organization_id": "org-redis"}'])
    redis_client = MagicMock()
    redis_client.pipeline.return_value = pipeline

    blocked, ctx = await resolve_session_context_from_redis(
        user_id="user-1",
        session_id="session-1",
        redis_client=redis_client,
    )

    assert blocked is False
    assert ctx == {"organization_id": "org-redis"}


@pytest.mark.asyncio
async def test_resolve_session_context_uses_redis_before_db():
    """Composed resolve returns Redis context without querying the database."""
    with patch(
        "libs.shared_utils.session_context_cache.resolve_session_context_from_redis",
        new=AsyncMock(return_value=(False, {"organization_id": "org-redis"})),
    ):
        ctx = await resolve_session_context(
            user_id="user-1",
            session_id="session-1",
            db_connection=AsyncMock(),
        )

    assert ctx == {"organization_id": "org-redis"}


@pytest.mark.asyncio
async def test_resolve_session_context_from_db_warms_redis():
    """DB resolver loads context and warms Redis."""

    class FakeRepo:
        """Minimal SessionRepository stand-in for DB fallback."""

        async def get_valid_session_context(self, session_id: str, db_connection=None):
            """Return a fixed organization context for the session."""
            assert session_id == "session-1"
            assert db_connection is not None
            return {"organization_id": "org-db"}

    db_connection = MagicMock()
    with (
        patch(
            "apps.user_service.app.db.repositories.get_session_repo",
            return_value=FakeRepo(),
        ),
        patch(
            "libs.shared_utils.session_context_cache.warm_session_context_cache",
            new=AsyncMock(),
        ) as warm_cache,
    ):
        ctx = await resolve_session_context_from_db(
            session_id="session-1",
            db_connection=db_connection,
        )

    assert ctx == {"organization_id": "org-db"}
    warm_cache.assert_awaited_once_with("session-1", "org-db")


@pytest.mark.asyncio
async def test_resolve_session_context_falls_back_to_db():
    """Cache miss loads context from the database and warms Redis."""

    class FakeRepo:
        """Minimal SessionRepository stand-in for DB fallback."""

        async def get_valid_session_context(self, session_id: str, db_connection=None):
            """Return a fixed organization context for the session."""
            assert session_id == "session-1"
            assert db_connection is not None
            return {"organization_id": "org-db"}

    db_connection = MagicMock()
    with (
        patch(
            "apps.user_service.app.db.repositories.get_session_repo",
            return_value=FakeRepo(),
        ),
        patch(
            "libs.shared_utils.session_context_cache.resolve_session_context_from_redis",
            new=AsyncMock(return_value=(False, None)),
        ),
        patch(
            "libs.shared_utils.session_context_cache.warm_session_context_cache",
            new=AsyncMock(),
        ) as warm_cache,
    ):
        ctx = await resolve_session_context(
            user_id="user-1",
            session_id="session-1",
            db_connection=db_connection,
        )

    assert ctx == {"organization_id": "org-db"}
    warm_cache.assert_awaited_once_with("session-1", "org-db")


@pytest.mark.asyncio
async def test_resolve_session_context_blocks_deleted_user():
    """Deleted users cannot resolve session context."""
    with patch(
        "libs.shared_utils.session_context_cache.resolve_session_context_from_redis",
        new=AsyncMock(return_value=(True, None)),
    ):
        ctx = await resolve_session_context(
            user_id="user-1",
            session_id="session-1",
            db_connection=AsyncMock(),
        )

    assert ctx is None


@pytest.mark.asyncio
async def test_resolve_session_context_blocks_revoked_session():
    """Revoked sessions cannot resolve session context."""
    with patch(
        "libs.shared_utils.session_context_cache.resolve_session_context_from_redis",
        new=AsyncMock(return_value=(True, None)),
    ):
        ctx = await resolve_session_context(
            user_id="user-1",
            session_id="session-1",
            db_connection=AsyncMock(),
        )

    assert ctx is None


@pytest.mark.asyncio
async def test_warm_session_context_cache_writes_redis():
    """Warming writes session context to Redis."""
    redis_client = AsyncMock()
    with patch(
        "libs.shared_utils.session_context_cache.get_redis",
        new=AsyncMock(return_value=redis_client),
    ):
        await warm_session_context_cache("session-1", "org-1")

    redis_client.setex.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalidate_ctx_cache_sets_revoked():
    """Invalidation deletes context and sets a revocation marker."""
    redis_client = AsyncMock()
    with patch(
        "libs.shared_utils.session_context_cache.get_redis",
        new=AsyncMock(return_value=redis_client),
    ):
        await invalidate_session_context_cache("session-1")

    redis_client.delete.assert_awaited_once()
    redis_client.setex.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalidate_user_sessions_marks_deleted():
    """User invalidation marks deletion and revokes known sessions."""
    redis_client = AsyncMock()
    with (
        patch(
            "libs.shared_utils.session_context_cache.get_redis",
            new=AsyncMock(return_value=redis_client),
        ),
        patch(
            "libs.shared_utils.session_context_cache.invalidate_session_context_cache",
            new=AsyncMock(),
        ) as invalidate_session,
    ):
        await invalidate_user_sessions_cache("user-1", ["session-1", "session-2"])

    redis_client.setex.assert_awaited_once()
    assert invalidate_session.await_count == 2


@pytest.mark.asyncio
async def test_revoke_org_member_sessions_db_redis():
    """Org-member revocation updates Postgres and invalidates Redis."""

    class FakeRepo:
        """Minimal SessionRepository stand-in for org-member revocation."""

        def __init__(self, db_connection):
            """Store the injected connection."""
            self.db_connection = db_connection

        async def revoke_org_sessions_for_user(self, user_id, organization_id):
            """Return session IDs revoked for the org member."""
            assert user_id == "user-1"
            assert organization_id == "org-1"
            return ["session-1", "session-2"]

    with (
        patch(
            "apps.user_service.app.db.repositories.SessionRepository",
            FakeRepo,
        ),
        patch(
            "libs.shared_utils.session_context_cache.invalidate_session_context_cache",
            new=AsyncMock(),
        ) as invalidate_session,
    ):
        await revoke_org_member_sessions_everywhere(
            db_connection=AsyncMock(),
            user_id="user-1",
            organization_id="org-1",
        )

    assert invalidate_session.await_count == 2


@pytest.mark.asyncio
async def test_revoke_org_sessions_everywhere_db_redis():
    """Organization revocation updates Postgres and invalidates Redis."""

    class FakeRepo:
        """Minimal SessionRepository stand-in for org-wide revocation."""

        def __init__(self, db_connection):
            """Store the injected connection."""
            self.db_connection = db_connection

        async def revoke_all_sessions_for_organization(self, organization_id):
            """Return all session IDs revoked for the organization."""
            assert organization_id == "org-1"
            return ["session-a", "session-b"]

    with (
        patch(
            "apps.user_service.app.db.repositories.SessionRepository",
            FakeRepo,
        ),
        patch(
            "libs.shared_utils.session_context_cache.invalidate_session_context_cache",
            new=AsyncMock(),
        ) as invalidate_session,
    ):
        await revoke_organization_sessions_everywhere(
            db_connection=AsyncMock(),
            organization_id="org-1",
        )

    assert invalidate_session.await_count == 2
