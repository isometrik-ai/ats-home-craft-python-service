"""Unit tests for session context cache helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.shared_utils.session_context_cache import (
    coalesced_resolve_session_context_from_db,
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
    warm_cache.assert_awaited_once_with("session-1", "org-db", redis_client=None)


@pytest.mark.asyncio
async def test_resolve_session_context_falls_back_to_db():
    """Cache miss loads context via coalesced DB resolver."""

    with (
        patch(
            "libs.shared_utils.session_context_cache.resolve_session_context_from_redis",
            new=AsyncMock(return_value=(False, None)),
        ),
        patch(
            "libs.shared_utils.session_context_cache.coalesced_resolve_session_context_from_db",
            new=AsyncMock(return_value={"organization_id": "org-db"}),
        ) as coalesced_db,
    ):
        ctx = await resolve_session_context(
            user_id="user-1",
            session_id="session-1",
            db_connection=AsyncMock(),
        )

    assert ctx == {"organization_id": "org-db"}
    coalesced_db.assert_awaited_once()
    assert coalesced_db.await_args.args == ("session-1",)


@pytest.mark.asyncio
async def test_coalesced_db_resolve_one_query():
    """Concurrent DB misses for one session run a single repository lookup."""

    calls = 0

    class FakeRepo:
        """Minimal SessionRepository stand-in for coalesced DB fallback."""

        async def get_valid_session_context(self, session_id: str, db_connection=None):
            """Return session context and count how many times the repo was queried."""
            nonlocal calls
            calls += 1
            assert session_id == "session-1"
            assert db_connection is not None
            await asyncio.sleep(0.05)
            return {"organization_id": "org-1"}

    class FakeAcquire:
        """Yield a mock connection without touching the real pool."""

        def __init__(self, pool):
            del pool
            self.conn = MagicMock()

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, exc_tb):
            del exc_type, exc, exc_tb
            return False

    with (
        patch(
            "apps.user_service.app.db.repositories.get_session_repo",
            return_value=FakeRepo(),
        ),
        patch(
            "libs.shared_utils.session_context_cache.get_pool",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "libs.shared_utils.session_context_cache.AcquireConnection",
            FakeAcquire,
        ),
        patch(
            "libs.shared_utils.session_context_cache.warm_session_context_cache",
            new=AsyncMock(),
        ),
    ):
        results = await asyncio.gather(
            *[coalesced_resolve_session_context_from_db("session-1") for _ in range(20)]
        )

    assert calls == 1
    assert all(result == {"organization_id": "org-1"} for result in results)


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


def test_extract_session_id_from_access_token():
    """JWT session_id claim is extracted without verification."""
    import jwt

    from libs.shared_utils.session_context_cache import (
        extract_session_id_from_access_token,
    )

    token = jwt.encode({"session_id": "sess-abc"}, "secret", algorithm="HS256")
    assert extract_session_id_from_access_token(token) == "sess-abc"
    assert extract_session_id_from_access_token(None) is None
    assert extract_session_id_from_access_token("not-a-jwt") is None


@pytest.mark.asyncio
async def test_redis_resolve_blocks_deleted_user():
    """Deleted-user marker blocks session resolution."""
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(return_value=[1, 0, None])
    redis_client = MagicMock()
    redis_client.pipeline.return_value = pipeline

    blocked, ctx = await resolve_session_context_from_redis(
        user_id="user-1",
        session_id="session-1",
        redis_client=redis_client,
    )
    assert blocked is True
    assert ctx is None


@pytest.mark.asyncio
async def test_redis_resolve_blocks_revoked_session():
    """Revoked-session marker blocks session resolution."""
    pipeline = MagicMock()
    pipeline.execute = AsyncMock(return_value=[0, 1, None])
    redis_client = MagicMock()
    redis_client.pipeline.return_value = pipeline

    blocked, ctx = await resolve_session_context_from_redis(
        user_id="user-1",
        session_id="session-1",
        redis_client=redis_client,
    )
    assert blocked is True
    assert ctx is None


@pytest.mark.asyncio
async def test_warm_session_context_after_auth_delegates():
    """warm_session_context_after_auth warms cache when session id is present."""
    with patch(
        "libs.shared_utils.session_context_cache.warm_session_context_cache",
        new=AsyncMock(),
    ) as warm_cache:
        from libs.shared_utils.session_context_cache import (
            warm_session_context_after_auth,
        )

        await warm_session_context_after_auth(session_id="session-1", organization_id="org-1")
    warm_cache.assert_awaited_once_with("session-1", "org-1")


@pytest.mark.asyncio
async def test_resolve_session_context_from_db_not_found():
    """DB resolver returns None when session is missing."""

    class FakeRepo:
        """SessionRepository double returning no context."""

        async def get_valid_session_context(self, session_id: str, db_connection=None):
            del session_id, db_connection
            return None

    with patch(
        "apps.user_service.app.db.repositories.get_session_repo",
        return_value=FakeRepo(),
    ):
        ctx = await resolve_session_context_from_db(
            session_id="missing",
            db_connection=MagicMock(),
        )
    assert ctx is None


@pytest.mark.asyncio
async def test_invalidate_session_context_cache_noop_when_disabled(monkeypatch):
    """Invalidation is skipped when cache is disabled."""
    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache._settings",
        lambda: SimpleNamespace(session_ctx_cache_enabled=False),
    )
    with patch(
        "libs.shared_utils.session_context_cache.get_redis",
        new=AsyncMock(side_effect=AssertionError("should not call redis")),
    ):
        await invalidate_session_context_cache("session-1")


@pytest.mark.asyncio
async def test_is_user_deleted_and_revoked_helpers(monkeypatch):
    """Direct Redis marker checks return True when keys exist."""
    from libs.shared_utils.session_context_cache import (
        _is_session_revoked,
        _is_user_deleted,
    )

    redis_client = AsyncMock()
    redis_client.exists = AsyncMock(return_value=1)
    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=redis_client),
    )
    assert await _is_user_deleted("user-1") is True
    assert await _is_session_revoked("session-1") is True


@pytest.mark.asyncio
async def test_get_redis_session_context_hit():
    """Direct Redis get returns parsed session context."""
    from libs.shared_utils.session_context_cache import _get_redis_session_context

    redis_client = AsyncMock()
    redis_client.get = AsyncMock(return_value='{"organization_id": "org-1"}')
    with patch(
        "libs.shared_utils.session_context_cache.get_redis",
        new=AsyncMock(return_value=redis_client),
    ):
        ctx = await _get_redis_session_context("session-1")
    assert ctx == {"organization_id": "org-1"}


def test_parse_session_context_payload_invalid() -> None:
    """Invalid cached JSON returns None."""
    from libs.shared_utils.session_context_cache import _parse_session_context_payload

    assert _parse_session_context_payload(None) is None
    assert _parse_session_context_payload("{bad") is None


@pytest.mark.asyncio
async def test_is_user_deleted_redis_error(monkeypatch) -> None:
    """Redis failures during deleted-user check return False."""
    from libs.shared_utils.session_context_cache import _is_user_deleted

    redis_client = AsyncMock()
    redis_client.exists = AsyncMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr(
        "libs.shared_utils.session_context_cache.get_redis",
        AsyncMock(return_value=redis_client),
    )
    assert await _is_user_deleted("user-1") is False


@pytest.mark.asyncio
async def test_get_user_from_auth_redis_success() -> None:
    """get_user_from_auth_redis finalizes user when Redis resolves context."""
    from starlette.requests import Request

    from libs.shared_middleware.jwt_auth import get_user_from_auth_redis

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user = {"sub": "u1", "email": "u@example.com", "session_id": "s1"}
    with patch(
        "libs.shared_middleware.jwt_auth.resolve_session_context_from_redis",
        AsyncMock(return_value=(False, {"organization_id": "org-1"})),
    ):
        user = await get_user_from_auth_redis(request, redis_client=MagicMock())
    assert user is not None
    assert user["_session_context"]["organization_id"] == "org-1"
