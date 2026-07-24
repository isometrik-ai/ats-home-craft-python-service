"""Unit tests for asyncpg pool helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from libs.shared_db.drivers import asyncpg_client


@pytest.fixture(autouse=True)
def _reset_pool_holder():
    """Ensure each test starts with no cached pool."""
    asyncpg_client._pool_holder["pool"] = None
    yield
    asyncpg_client._pool_holder["pool"] = None


def test_build_dsn_from_url(monkeypatch) -> None:
    """_build_dsn prefers DATABASE_URL when set."""
    db = MagicMock()
    db.url = "postgresql://u:p@host/db"
    monkeypatch.setattr(asyncpg_client, "shared_settings", MagicMock(database=db))
    assert asyncpg_client._build_dsn() == "postgresql://u:p@host/db"


def test_build_dsn_from_parts(monkeypatch) -> None:
    """_build_dsn composes DSN from individual settings."""
    db = MagicMock()
    db.url = None
    db.host = "localhost"
    db.port = 5432
    db.database = "app"
    db.db_user = "user"
    db.password = "secret"
    monkeypatch.setattr(asyncpg_client, "shared_settings", MagicMock(database=db))
    assert asyncpg_client._build_dsn() == "postgresql://user:secret@localhost:5432/app"


def test_make_ssl_context_disable(monkeypatch) -> None:
    """disable ssl mode returns None."""
    db = MagicMock()
    db.ssl_mode = "disable"
    monkeypatch.setattr(asyncpg_client, "shared_settings", MagicMock(database=db))
    assert asyncpg_client._make_ssl_context() is None


def test_make_ssl_context_require(monkeypatch) -> None:
    """require ssl mode builds a TLS context."""
    db = MagicMock()
    db.ssl_mode = "require"
    monkeypatch.setattr(asyncpg_client, "shared_settings", MagicMock(database=db))
    ctx = asyncpg_client._make_ssl_context()
    assert ctx is not None


def test_make_ssl_context_verify_full(monkeypatch) -> None:
    """verify-full ssl mode uses CA file."""
    db = MagicMock()
    db.ssl_mode = "verify-full"
    db.ssl_root_cert = None
    monkeypatch.setattr(asyncpg_client, "shared_settings", MagicMock(database=db))
    ctx = asyncpg_client._make_ssl_context()
    assert ctx is not None


def test_make_ssl_context_unknown_mode(monkeypatch) -> None:
    """Unknown ssl mode falls back to disable."""
    db = MagicMock()
    db.ssl_mode = "unknown-mode"
    monkeypatch.setattr(asyncpg_client, "shared_settings", MagicMock(database=db))
    assert asyncpg_client._make_ssl_context() is None


@pytest.mark.asyncio
async def test_init_connection_factory_sets_timeout() -> None:
    """Connection initializer executes statement_timeout SET."""
    factory = asyncpg_client._init_connection_factory(5000)
    assert factory is not None
    conn = AsyncMock()
    await factory(conn)
    conn.execute.assert_awaited_once_with("SET statement_timeout TO 5000")


def test_init_connection_factory_none_when_unset() -> None:
    """No initializer when statement_timeout_ms is None."""
    assert asyncpg_client._init_connection_factory(None) is None


@pytest.mark.asyncio
async def test_get_pool_creates_once(monkeypatch) -> None:
    """get_pool lazily creates and caches the shared pool."""
    mock_pool = MagicMock()
    create_pool = AsyncMock(return_value=mock_pool)
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    db = MagicMock()
    db.url = "postgresql://u:p@host/db"
    db.min_pool = 1
    db.max_pool = 5
    db.command_timeout = 30.0
    db.statement_timeout_ms = None
    db.max_idle_time = 300.0
    db.ssl_mode = "disable"
    monkeypatch.setattr(asyncpg_client, "shared_settings", MagicMock(database=db))

    first = await asyncpg_client.get_pool()
    second = await asyncpg_client.get_pool()

    assert first is second is mock_pool
    create_pool.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_pool_resets_holder() -> None:
    """close_pool closes and clears the cached pool."""
    mock_pool = AsyncMock()
    asyncpg_client._pool_holder["pool"] = mock_pool
    await asyncpg_client.close_pool()
    mock_pool.close.assert_awaited_once()
    assert asyncpg_client._pool_holder["pool"] is None


@pytest.mark.asyncio
async def test_acquire_connection_success() -> None:
    """AcquireConnection returns a connection on first attempt."""
    mock_conn = MagicMock()
    mock_pool = MagicMock()
    mock_pool.acquire = AsyncMock(return_value=mock_conn)
    mock_pool.release = AsyncMock()

    db = MagicMock()
    db.acquire_max_retry = 3
    db.acquire_base_delay = 0.01
    db.acquire_timeout = 1.0

    with patch.object(asyncpg_client, "shared_settings", MagicMock(database=db)):
        async with asyncpg_client.AcquireConnection(mock_pool) as conn:
            assert conn is mock_conn
        mock_pool.release.assert_awaited_once_with(mock_conn)


@pytest.mark.asyncio
async def test_acquire_connection_retries_then_raises(monkeypatch) -> None:
    """AcquireConnection retries transient failures then raises."""
    mock_pool = MagicMock()
    mock_pool.acquire = AsyncMock(side_effect=asyncio.TimeoutError("timeout"))

    db = MagicMock()
    db.acquire_max_retry = 2
    db.acquire_base_delay = 0.0
    db.acquire_timeout = 0.1

    monkeypatch.setattr(asyncpg_client, "shared_settings", MagicMock(database=db))
    monkeypatch.setattr(asyncpg_client.random, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(asyncpg_client.asyncio, "sleep", AsyncMock())

    with pytest.raises(asyncio.TimeoutError):
        async with asyncpg_client.AcquireConnection(mock_pool):
            pass


@pytest.mark.asyncio
async def test_acquire_connection_retries_generic_exception(monkeypatch) -> None:
    """AcquireConnection retries generic exceptions before failing."""
    mock_conn = MagicMock()
    mock_pool = MagicMock()
    mock_pool.acquire = AsyncMock(side_effect=[RuntimeError("boom"), mock_conn])
    mock_pool.release = AsyncMock()

    db = MagicMock()
    db.acquire_max_retry = 2
    db.acquire_base_delay = 0.0
    db.acquire_timeout = 0.1

    monkeypatch.setattr(asyncpg_client, "shared_settings", MagicMock(database=db))
    monkeypatch.setattr(asyncpg_client.random, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(asyncpg_client.asyncio, "sleep", AsyncMock())

    async with asyncpg_client.AcquireConnection(mock_pool) as conn:
        assert conn is mock_conn


@pytest.mark.asyncio
async def test_get_connection_returns_acquire_helper(monkeypatch) -> None:
    """get_connection wraps pool acquisition."""
    mock_pool = MagicMock()
    monkeypatch.setattr(asyncpg_client, "get_pool", AsyncMock(return_value=mock_pool))
    helper = await asyncpg_client.get_connection()
    assert isinstance(helper, asyncpg_client.AcquireConnection)
    assert helper.pool is mock_pool
