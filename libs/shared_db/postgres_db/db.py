"""
Module for managing PostgreSQL database connections and interactions.
This module provides a centralized interface for managing
PostgreSQL database connections, loading environment variables,
and creating a Supabase client for database operations.
"""

# pylint: disable=import-error
import os
import sys
import asyncio

# Third-party imports
import psycopg2
from psycopg2 import pool
import asyncpg
import random

# Local application imports
from dotenv import load_dotenv
from supabase import create_client
from contextlib import asynccontextmanager

# Configure import paths first
base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
monorepo_root = os.path.abspath(os.path.join(base_path, "../.."))

# Add necessary paths to sys.path
sys.path.insert(0, base_path)
sys.path.insert(0, monorepo_root)

# Load environment variables from .env
load_dotenv(os.path.join(monorepo_root, ".env"))
pool_lock = asyncio.Lock()


DB_PORT = os.getenv("DB_PORT", None)
DB_HOST = os.getenv("DB_HOST", None)
DB_DATABASE = os.getenv("DB_DATABASE", None)
DB_USER = os.getenv("DB_USER", None)
DB_PASSWORD = os.getenv("DB_PASSWORD", None)

# Connection pool configuration
MIN_CONNECTIONS = 5  # Increased from 5
MAX_CONNECTIONS = 15
POOL_TIMEOUT = 10  # Increased from 10 seconds for load testing
COMMAND_TIMEOUT = 1  # Increased from 20 seconds for load testing

# Synchronous connection pool
connection_pool = None

# Async connection pool
async_connection_pool = None


async def get_async_connection_pool():
    """
    Get or create an async connection pool for database connections.
    Uses a lock to prevent race conditions.
    """
    global async_connection_pool

    if async_connection_pool is None:
        async with pool_lock:
            if async_connection_pool is None:  # double-check inside lock
                try:
                    async_connection_pool = await asyncpg.create_pool(
                        host=DB_HOST,
                        database=DB_DATABASE,
                        user=DB_USER,
                        password=DB_PASSWORD,
                        port=DB_PORT,
                        min_size=MIN_CONNECTIONS,
                        max_size=MAX_CONNECTIONS,
                        timeout=POOL_TIMEOUT,
                        command_timeout=COMMAND_TIMEOUT,
                        max_inactive_connection_lifetime=300,  # Increased from 60
                    )
                    print(
                        f"Async connection pool created with {MIN_CONNECTIONS}-{MAX_CONNECTIONS} connections"
                    )
                except Exception as e:
                    print(f"Error creating async connection pool: {e}")
                    raise
    return async_connection_pool


async def get_async_db_conn():
    """FastAPI dependency yielding an asyncpg connection from the pool with optimized retries for load testing."""
    pool = await get_async_connection_pool()
    conn = None
    last_exc = None
    # conn = await asyncio.wait_for(pool.acquire(), timeout=POOL_TIMEOUT)

    # Optimized retry logic for load testing
    for attempt in range(3):  # Reduced from 5 to 3 attempts
        try:
            conn = await asyncio.wait_for(pool.acquire(), timeout=POOL_TIMEOUT)
            break  # success
        except asyncio.TimeoutError:
            print(f"[DB] Attempt {attempt+1}: connection timeout after {POOL_TIMEOUT}s")
            last_exc = Exception(
                "Database connection timeout - server may be down or overloaded"
            )
            # Shorter backoff for load testing
            await asyncio.sleep(min(1 + attempt, 3))
        except Exception as e:
            print(f"[DB] Attempt {attempt+1}: error acquiring connection: {e}")
            last_exc = e
            await asyncio.sleep(min(1 + attempt, 3))
    else:
        # all retries failed
        raise last_exc or Exception(
            "Database connection timeout - server may be down or overloaded"
        )

    try:
        yield conn
    finally:
        if conn:
            try:
                await pool.release(conn)
            except Exception as e:
                print(f"[DB] Error releasing connection: {e}")
