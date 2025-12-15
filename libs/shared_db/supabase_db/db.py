"""Module for managing PostgreSQL database connections and interactions.
This module provides a centralized interface for managing
PostgreSQL database connections, loading environment variables,
and creating a Supabase client for database operations.
"""

import os

from httpx import AsyncClient as HTTPXAsyncClient
from supabase import AsyncClient, ClientOptions, create_async_client

from libs.shared_db.common import setup_import_paths_and_env

setup_import_paths_and_env()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_KEY")


class SupabaseClientCache:
    """Singleton class to manage Supabase client instances."""

    _instance = None
    _supabase_client: AsyncClient | None = None
    _supabase_admin_client: AsyncClient | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_client(self) -> AsyncClient:
        """Get or create a cached Supabase client instance."""
        if self._supabase_client is None:
            self._supabase_client = await create_async_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        return self._supabase_client

    async def get_admin_client(self, http_client: HTTPXAsyncClient | None = None) -> AsyncClient:
        """Get or create a cached Supabase admin client instance.

        Requires SUPABASE_SERVICE_KEY to be set in the environment. If the key is
        missing, we fail fast with a clear error to avoid silently using the anon
        client and hitting RLS policies during privileged operations (e.g. audit writes).
        """
        if self._supabase_admin_client is None:
            if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
                raise RuntimeError(
                    "Missing Supabase admin configuration. Ensure SUPABASE_URL and "
                    "SUPABASE_SERVICE_KEY are set before starting the app."
                )

            if http_client is not None:
                options = ClientOptions(persist_session=False)
                self._supabase_admin_client = await create_async_client(
                    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, options=options
                )
            else:
                self._supabase_admin_client = await create_async_client(
                    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
                )

            try:
                await self._supabase_admin_client.auth.admin.list_users()
            except Exception as e:
                raise RuntimeError(
                    "Supabase admin client warm-up failed. Verify SUPABASE_SERVICE_KEY "
                    "is the service role key and has admin privileges."
                ) from e

        return self._supabase_admin_client

    async def get_fresh_admin_client(self) -> AsyncClient:
        """Get a fresh admin client instance, bypassing cache.
        Useful when the cached client gets into a corrupted state.
        """
        self.reset_admin_client()
        return await self.get_admin_client()

    def reset_admin_client(self):
        """Reset the admin client cache to force recreation."""
        self._supabase_admin_client = None


# Global cache instance
_cache = SupabaseClientCache()


async def get_supabase_client():
    """Get or create a cached Supabase client instance.
    Uses caching to improve performance by reusing the same client.
    """
    return await _cache.get_client()


async def get_supabase_admin_client(
    user_agent: str | None = None, custom_headers: dict | None = None
):
    """Get or create a cached Supabase admin client instance.
    Uses caching to improve performance by reusing the same client.

    Args:
        user_agent: Optional User-Agent string
        custom_headers: Optional dict of additional custom headers (e.g., X-Device-Signature)

    If a custom user_agent or custom_headers are provided, the cache is reset
    to ensure a new client is created with the custom headers.
    """
    if user_agent is not None or custom_headers is not None:
        # Reset cache when custom headers are provided
        reset_supabase_admin_client()
        # Build headers dict
        headers = {}
        if user_agent:
            headers["User-Agent"] = user_agent
        if custom_headers:
            headers.update(custom_headers)

        http_client = HTTPXAsyncClient(headers=headers)
    else:
        http_client = None
    return await _cache.get_admin_client(http_client=http_client)


def reset_supabase_admin_client():
    """Reset the admin client cache to force recreation.
    Useful when the admin client gets into a corrupted state.
    """
    _cache.reset_admin_client()


async def get_fresh_supabase_admin_client():
    """Get a fresh admin client instance, bypassing cache.
    Useful when the cached client gets into a corrupted state.
    """
    return await _cache.get_fresh_admin_client()
