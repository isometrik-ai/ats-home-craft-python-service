"""AsyncPG client and pool management helpers.

This module centralizes asyncpg pool creation, acquisition, and teardown,
with environment-driven configuration for connection details, pooling, and
timeouts. Use `get_pool()` for lazily initialized access, and the `acquire`
context manager when you need a single connection from the pool.

Production-minded options (env):
- DATABASE_URL (preferred) or DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD
- DB_MIN_POOL (default 1), DB_MAX_POOL (default 10)
- DB_COMMAND_TIMEOUT (seconds, default 30)
- DB_STATEMENT_TIMEOUT_MS (per-connection; optional)
- DB_SSL_MODE: disable | require | verify-full (default disable)
- DB_SSL_ROOT_CERT: path to CA for verify-full
- DB_ACQUIRE_MAX_RETRY (default 3)
- DB_ACQUIRE_BASE_DELAY (seconds, default 1.0) + jitter
- DB_ACQUIRE_TIMEOUT (seconds, optional)
- DB_MAX_IDLE_TIME (seconds; passed to asyncpg max_inactive_connection_lifetime)
"""

import asyncio
import logging
import random
import ssl
from collections.abc import Awaitable, Callable

import asyncpg

from libs.shared_config.app_settings import shared_settings

# Internal singleton pool reference kept in a holder to avoid globals
_pool_holder: dict[str, asyncpg.Pool | None] = {"pool": None}


def _build_dsn() -> str:
    """Build the database DSN from environment variables.

    Prefers a single DATABASE_URL; otherwise composes from individual parts.
    """
    dsn = shared_settings.database.url
    if dsn:
        return dsn

    host = shared_settings.database.host
    port = shared_settings.database.port
    name = shared_settings.database.database
    user = shared_settings.database.db_user
    password = shared_settings.database.password

    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def _make_ssl_context() -> ssl.SSLContext | None:
    """Build an SSL context based on DB_SSL_MODE."""
    mode = shared_settings.database.ssl_mode
    if mode == "disable":
        return None
    if mode == "require":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if mode == "verify-full":
        cafile = shared_settings.database.ssl_root_cert
        ctx = ssl.create_default_context(cafile=cafile)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx
    # Fallback to disable if unknown
    return None


def _init_connection_factory(
    statement_timeout_ms: int | None,
) -> Callable[[asyncpg.Connection], Awaitable[None]] | None:
    """Optionally create a connection initializer to set statement_timeout."""
    if statement_timeout_ms is None:
        return None

    async def _init(conn: asyncpg.Connection) -> None:
        await conn.execute(f"SET statement_timeout TO {statement_timeout_ms}")

    return _init


async def get_pool() -> asyncpg.Pool:
    """Lazily create and return the shared asyncpg pool.

    Config (env):
        DATABASE_URL (preferred) or DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD
        DB_MIN_POOL (default 1)
        DB_MAX_POOL (default 10)
        DB_COMMAND_TIMEOUT (seconds, default 30.0)
        DB_STATEMENT_TIMEOUT_MS (optional, milliseconds)
        DB_SSL_MODE (disable/require/verify-full)
        DB_SSL_ROOT_CERT (path, used when verify-full)
        DB_ACQUIRE_MAX_RETRY (default 3)
        DB_ACQUIRE_BASE_DELAY (seconds, default 1.0)
        DB_ACQUIRE_TIMEOUT (seconds, default 10.0)
        DB_MAX_IDLE_TIME (seconds, default 300.0)
    """
    pool = _pool_holder["pool"]
    if pool is not None:
        return pool

    dsn = _build_dsn()
    # Defaults align with .env (see DB_MIN_POOL, DB_MAX_POOL, etc.)
    min_size = shared_settings.database.min_pool
    max_size = shared_settings.database.max_pool
    command_timeout = shared_settings.database.command_timeout
    ssl_context = _make_ssl_context()
    max_idle = shared_settings.database.max_idle_time

    statement_timeout_ms = shared_settings.database.statement_timeout_ms
    init_cb = _init_connection_factory(statement_timeout_ms)

    pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
        init=init_cb,
        ssl=ssl_context,
        max_inactive_connection_lifetime=max_idle,
    )
    _pool_holder["pool"] = pool
    return pool


async def close_pool() -> None:
    """Close and reset the shared pool."""
    pool = _pool_holder["pool"]
    if pool:
        await pool.close()
        _pool_holder["pool"] = None


class AcquireConnection:
    """Async context manager to acquire and release a connection from the pool."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self.conn: asyncpg.Connection | None = None

    async def __aenter__(self) -> asyncpg.Connection:
        max_attempts = shared_settings.database.acquire_max_retry
        base_delay = shared_settings.database.acquire_base_delay
        acquire_timeout = shared_settings.database.acquire_timeout

        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                self.conn = await self.pool.acquire(timeout=acquire_timeout)
                return self.conn
            except (asyncio.TimeoutError, asyncpg.TooManyConnectionsError) as exc:
                last_exc = exc
                if attempt == max_attempts:
                    break
                jitter = random.uniform(0, base_delay)
                delay = min(base_delay * (2 ** (attempt - 1)) + jitter, 10.0)
                logging.getLogger(__name__).warning(
                    "DB acquire attempt %s/%s failed (%s). Retrying in %.2fs",
                    attempt,
                    max_attempts,
                    exc.__class__.__name__,
                    delay,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                last_exc = exc
                if attempt == max_attempts:
                    break
                jitter = random.uniform(0, base_delay)
                delay = min(base_delay * (2 ** (attempt - 1)) + jitter, 10.0)
                logging.getLogger(__name__).warning(
                    "DB acquire attempt %s/%s failed (%s). Retrying in %.2fs",
                    attempt,
                    max_attempts,
                    exc.__class__.__name__,
                    delay,
                )
                await asyncio.sleep(delay)

        # Exhausted retries
        raise last_exc or RuntimeError("Failed to acquire database connection")

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.conn is not None:
            await self.pool.release(self.conn)
            self.conn = None


async def get_connection():
    """Acquire a single connection (helper when dependency injection prefers callables)."""
    pool = await get_pool()
    return AcquireConnection(pool)
