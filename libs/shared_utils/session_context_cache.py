"""Session context cache: Redis → Postgres."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import asyncpg
import jwt
import redis.asyncio as redis

from libs.shared_config.app_settings import shared_settings
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_db.drivers.redis_client import get_redis
from libs.shared_utils.logger import get_logger

logger = get_logger(__name__)

SESSION_CTX_KEY_PREFIX = "session:ctx:"
SESSION_REVOKED_KEY_PREFIX = "session:revoked:"
USER_DELETED_KEY_PREFIX = "user:deleted:"

T = TypeVar("T")


class _AsyncSingleflight:
    """Coalesce concurrent async work per key (one leader, many waiters)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._in_flight: dict[str, asyncio.Task[Any]] = {}

    async def run_coalesced(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        """Run ``fn`` once per ``key``; concurrent callers await the same task."""
        async with self._lock:
            task = self._in_flight.get(key)
            if task is None:
                task = asyncio.create_task(self._run(key, fn))
                self._in_flight[key] = task
        return await task

    async def _run(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute ``fn`` and clear the in-flight entry for ``key`` when done."""
        try:
            return await fn()
        finally:
            async with self._lock:
                if self._in_flight.get(key) is asyncio.current_task():
                    self._in_flight.pop(key, None)


_session_ctx_db_singleflight = _AsyncSingleflight()


def _settings():
    """Return Redis-related settings."""
    return shared_settings.redis


def _session_ctx_key(session_id: str) -> str:
    """Build Redis key for cached session context."""
    return f"{SESSION_CTX_KEY_PREFIX}{session_id}"


def _session_revoked_key(session_id: str) -> str:
    """Build Redis key for session revocation marker."""
    return f"{SESSION_REVOKED_KEY_PREFIX}{session_id}"


def _user_deleted_key(user_id: str) -> str:
    """Build Redis key for user-deleted marker."""
    return f"{USER_DELETED_KEY_PREFIX}{user_id}"


def extract_session_id_from_access_token(access_token: str | None) -> str | None:
    """Read session_id from a Supabase access token without a network round trip."""
    if not access_token:
        return None
    try:
        claims = jwt.decode(access_token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    session_id = claims.get("session_id") or claims.get("jti")
    return str(session_id) if session_id else None


async def _is_user_deleted(user_id: str | None) -> bool:
    """Return True when the user is marked deleted in Redis."""
    if not user_id or not _settings().session_ctx_cache_enabled:
        return False
    redis_client = await get_redis()
    if redis_client is None:
        return False
    try:
        return bool(await redis_client.exists(_user_deleted_key(user_id)))
    except Exception as exc:
        logger.warning("Redis user-deleted check failed: %s", exc)
        return False


async def _is_session_revoked(session_id: str | None) -> bool:
    """Return True when the session is marked revoked in Redis."""
    if not session_id or not _settings().session_ctx_cache_enabled:
        return False
    redis_client = await get_redis()
    if redis_client is None:
        return False
    try:
        return bool(await redis_client.exists(_session_revoked_key(session_id)))
    except Exception as exc:
        logger.warning("Redis session-revoked check failed: %s", exc)
        return False


def _parse_session_context_payload(raw: str | bytes | None) -> dict[str, Any] | None:
    """Parse a cached session-context JSON payload."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        org_id = data.get("organization_id")
        return {"organization_id": str(org_id) if org_id is not None else None}
    except (json.JSONDecodeError, TypeError):
        return None


async def _fetch_redis_session_state(
    redis_client: redis.Redis | None,
    user_id: str | None,
    session_id: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Read session state from Redis in one pipeline.

    Returns:
        (blocked, context) where blocked=True means deleted/revoked markers were found.
    """
    if not _settings().session_ctx_cache_enabled or redis_client is None:
        return False, None

    try:
        pipe = redis_client.pipeline()
        if user_id:
            pipe.exists(_user_deleted_key(user_id))
        pipe.exists(_session_revoked_key(session_id))
        pipe.get(_session_ctx_key(session_id))
        results = await pipe.execute()
    except Exception as exc:
        logger.warning("Redis session-state pipeline failed: %s", exc)
        return False, None

    idx = 0
    if user_id:
        if results[idx]:
            logger.info(
                "session context blocked user_id=%s session_id=%s reason=user_deleted",
                user_id,
                session_id,
            )
            return True, None
        idx += 1

    if results[idx]:
        logger.info(
            "session context blocked session_id=%s reason=session_revoked",
            session_id,
        )
        return True, None

    ctx = _parse_session_context_payload(results[idx + 1])
    if ctx is not None:
        logger.debug(
            "session context cache hit session_id=%s organization_id=%s",
            session_id,
            ctx.get("organization_id"),
        )
    return False, ctx


async def _get_redis_session_context(session_id: str) -> dict[str, Any] | None:
    """Read session context from Redis, or None on miss/error."""
    redis_client = await get_redis()
    if redis_client is None:
        return None
    try:
        raw = await redis_client.get(_session_ctx_key(session_id))
        return _parse_session_context_payload(raw)
    except Exception as exc:
        logger.warning("Redis session-context read failed: %s", exc)
        return None


async def resolve_session_context_from_redis(
    *,
    user_id: str | None,
    session_id: str | None,
    redis_client: redis.Redis | None,
) -> tuple[bool, dict[str, Any] | None]:
    """Resolve session context from Redis only.

    Returns:
        (blocked, context). blocked=True when deletion/revocation markers are present.
    """
    if not session_id or not str(session_id).strip():
        return False, None
    return await _fetch_redis_session_state(redis_client, user_id, str(session_id))


async def resolve_session_context_from_db(
    *,
    session_id: str | None,
    db_connection: asyncpg.Connection,
    redis_client: redis.Redis | None = None,
) -> dict[str, Any] | None:
    """Resolve session context from Postgres and warm Redis. No Redis reads."""
    return await _resolve_session_context_from_db_impl(
        session_id=session_id,
        db_connection=db_connection,
        redis_client=redis_client,
    )


async def _resolve_session_context_from_db_impl(
    *,
    session_id: str | None,
    db_connection: asyncpg.Connection,
    redis_client: redis.Redis | None = None,
) -> dict[str, Any] | None:
    """Load session context from Postgres and warm Redis (single caller)."""
    if not session_id or not str(session_id).strip():
        return None

    from apps.user_service.app.db.repositories import (
        SessionRepository,
        get_session_repo,
    )

    session_id = str(session_id)
    session_repo: SessionRepository = get_session_repo()
    db_ctx = await session_repo.get_valid_session_context(session_id, db_connection)
    if db_ctx is None:
        logger.info("session context not found session_id=%s", session_id)
        return None

    logger.debug(
        "session context cache miss session_id=%s organization_id=%s source=db",
        session_id,
        db_ctx.get("organization_id"),
    )
    await warm_session_context_cache(
        session_id,
        db_ctx.get("organization_id"),
        redis_client=redis_client,
    )
    return db_ctx


async def coalesced_resolve_session_context_from_db(
    session_id: str | None,
    *,
    redis_client: redis.Redis | None = None,
) -> dict[str, Any] | None:
    """Resolve session context from Postgres with singleflight per session_id."""
    if not session_id or not str(session_id).strip():
        return None

    session_id = str(session_id)

    async def _leader() -> dict[str, Any] | None:
        pool = await get_pool()
        async with AcquireConnection(pool) as conn:
            return await _resolve_session_context_from_db_impl(
                session_id=session_id,
                db_connection=conn,
                redis_client=redis_client,
            )

    return await _session_ctx_db_singleflight.run_coalesced(session_id, _leader)


async def warm_session_context_cache(
    session_id: str,
    organization_id: str | None,
    redis_client: redis.Redis | None = None,
) -> None:
    """Write session context to Redis."""
    if not session_id or not _settings().session_ctx_cache_enabled:
        return

    client = redis_client if redis_client is not None else await get_redis()
    if client is None:
        return

    try:
        payload = json.dumps(
            {
                "organization_id": organization_id,
                "cached_at": int(time.time()),
            }
        )
        await client.setex(
            _session_ctx_key(session_id),
            _settings().session_ctx_cache_ttl_seconds,
            payload,
        )
        logger.debug(
            "session context cache warmed session_id=%s organization_id=%s ttl_seconds=%s",
            session_id,
            organization_id,
            _settings().session_ctx_cache_ttl_seconds,
        )
    except Exception as exc:
        logger.warning("Redis session-context warm failed: %s", exc)


async def warm_session_context_after_auth(
    *,
    session_id: str | None,
    organization_id: str | None,
) -> None:
    """Warm Redis after login or org selection."""
    if not session_id:
        return
    await warm_session_context_cache(session_id, organization_id)


async def invalidate_session_context_cache(session_id: str) -> None:
    """Invalidate positive cache and set session revocation marker."""
    if not session_id or not _settings().session_ctx_cache_enabled:
        return

    redis_client = await get_redis()
    if redis_client is None:
        return

    try:
        await redis_client.delete(_session_ctx_key(session_id))
        await redis_client.setex(
            _session_revoked_key(session_id),
            _settings().session_revoked_cache_ttl_seconds,
            "1",
        )
        logger.info("session context cache invalidated session_id=%s", session_id)
    except Exception as exc:
        logger.warning("Redis session-context invalidation failed: %s", exc)


async def revoke_org_member_sessions_everywhere(
    *,
    db_connection: asyncpg.Connection,
    user_id: str,
    organization_id: str,
) -> None:
    """Revoke org-member sessions in Postgres and invalidate Redis markers."""
    from apps.user_service.app.db.repositories import SessionRepository

    session_repo = SessionRepository(db_connection=db_connection)
    session_ids = await session_repo.revoke_org_sessions_for_user(user_id, organization_id)
    for session_id in session_ids:
        await invalidate_session_context_cache(session_id)
    if session_ids:
        logger.info(
            "revoked org-member sessions user_id=%s organization_id=%s count=%s",
            user_id,
            organization_id,
            len(session_ids),
        )


async def revoke_organization_sessions_everywhere(
    *,
    db_connection: asyncpg.Connection,
    organization_id: str,
) -> None:
    """Revoke all organization-linked sessions in Postgres and Redis."""
    from apps.user_service.app.db.repositories import SessionRepository

    session_repo = SessionRepository(db_connection=db_connection)
    session_ids = await session_repo.revoke_all_sessions_for_organization(organization_id)
    for session_id in session_ids:
        await invalidate_session_context_cache(session_id)
    if session_ids:
        logger.info(
            "revoked organization sessions organization_id=%s count=%s",
            organization_id,
            len(session_ids),
        )


async def invalidate_user_sessions_cache(
    user_id: str,
    session_ids: list[str] | None = None,
) -> None:
    """Mark user deleted and invalidate known session caches."""
    if not user_id or not _settings().session_ctx_cache_enabled:
        return

    redis_client = await get_redis()
    if redis_client is not None:
        try:
            await redis_client.setex(
                _user_deleted_key(user_id),
                _settings().user_deleted_cache_ttl_seconds,
                "1",
            )
        except Exception as exc:
            logger.warning("Redis user-deleted mark failed: %s", exc)

    if session_ids:
        for session_id in session_ids:
            await invalidate_session_context_cache(session_id)


async def resolve_session_context(
    *,
    user_id: str | None,
    session_id: str | None,
    db_connection: asyncpg.Connection | None = None,
    redis_client: redis.Redis | None = None,
) -> dict[str, Any] | None:
    """Resolve session context via Redis → DB (for extract_user_context fallback)."""
    client = redis_client if redis_client is not None else await get_redis()
    blocked, redis_ctx = await resolve_session_context_from_redis(
        user_id=user_id,
        session_id=session_id,
        redis_client=client,
    )
    if blocked:
        return None
    if redis_ctx is not None:
        return redis_ctx

    del db_connection
    return await coalesced_resolve_session_context_from_db(
        session_id,
        redis_client=client,
    )
