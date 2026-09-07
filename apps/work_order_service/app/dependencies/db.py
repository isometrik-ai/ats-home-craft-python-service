"""Database dependencies for work_order_service."""

from __future__ import annotations

from fastapi import Depends

from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_db.drivers.asyncpg_uow import UnitOfWork


async def db_pool():
    """Return the shared asyncpg pool."""
    return await get_pool()


async def db_conn(pool=Depends(db_pool)):
    """Yield a connection with work_order schema search path."""
    async with AcquireConnection(pool) as conn:
        await conn.execute("SET search_path TO work_order, public")
        yield conn


async def db_uow(pool=Depends(db_pool)):
    """Yield a transactional connection with work_order search path."""
    async with UnitOfWork(pool) as conn:
        await conn.execute("SET search_path TO work_order, public")
        yield conn
