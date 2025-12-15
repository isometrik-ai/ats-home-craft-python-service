"""Unit-of-Work helper for asyncpg.

Provides a simple async context manager that opens a transaction on an
acquired connection and handles commit/rollback automatically.
"""

from __future__ import annotations

import asyncpg

from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool


class UnitOfWork:
    """Async context manager for transactional work with asyncpg."""

    def __init__(self, pool: asyncpg.Pool | None = None):
        self.pool = pool
        self.conn_ctx = None
        self.conn: asyncpg.Connection | None = None
        self.tx = None

    async def __aenter__(self) -> asyncpg.Connection:
        pool = self.pool or await get_pool()
        self.conn_ctx = AcquireConnection(pool)
        self.conn = await self.conn_ctx.__aenter__()
        self.tx = self.conn.transaction()
        await self.tx.__aenter__()
        return self.conn

    async def __aexit__(self, exc_type, exc, traceback):
        if self.tx:
            await self.tx.__aexit__(exc_type, exc, traceback)
        if self.conn_ctx:
            await self.conn_ctx.__aexit__(exc_type, exc, traceback)
