"""Database dependency providers for FastAPI.

These helpers expose the shared asyncpg pool, a single-connection context,
and a transaction-scoped Unit of Work for routes and services.
"""

from __future__ import annotations

from fastapi import Depends

from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_db.drivers.asyncpg_uow import UnitOfWork


async def db_pool():
    """Return the shared asyncpg pool (lazy-initialized)."""
    return await get_pool()


async def db_conn(pool=Depends(db_pool)):
    """Yield a single connection acquired from the pool."""
    async with AcquireConnection(pool) as conn:
        yield conn


async def db_uow(pool=Depends(db_pool)):
    """Yield a connection inside a transaction (Unit of Work)."""
    async with UnitOfWork(pool) as conn:
        yield conn
