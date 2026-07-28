"""Unit tests for database FastAPI dependencies."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.dependencies import db as db_deps


@pytest.mark.asyncio
async def test_db_pool_returns_shared_pool():
    """db_pool delegates to get_pool."""
    mock_pool = MagicMock()
    with patch("apps.user_service.app.dependencies.db.get_pool", AsyncMock(return_value=mock_pool)):
        pool = await db_deps.db_pool()
    assert pool is mock_pool


@pytest.mark.asyncio
async def test_db_conn_yields_acquired_connection():
    """db_conn acquires a connection from the pool."""
    mock_conn = MagicMock()
    mock_pool = MagicMock()

    class _Acquire:
        def __init__(self, pool):
            self.pool = pool

        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            return False

    with patch("apps.user_service.app.dependencies.db.AcquireConnection", _Acquire):
        gen = db_deps.db_conn(mock_pool)
        conn = await gen.__anext__()
        assert conn is mock_conn
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()


@pytest.mark.asyncio
async def test_db_uow_yields_transaction_connection():
    """db_uow wraps UnitOfWork transaction scope."""
    mock_conn = MagicMock()
    mock_pool = MagicMock()

    class _Uow:
        def __init__(self, pool):
            self.pool = pool

        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            return False

    with patch("apps.user_service.app.dependencies.db.UnitOfWork", _Uow):
        gen = db_deps.db_uow(mock_pool)
        conn = await gen.__anext__()
        assert conn is mock_conn
