"""
Module for managing PostgreSQL database connections and interactions.
This module provides a centralized interface for managing
PostgreSQL database connections, loading environment variables,
and creating a Supabase client for database operations.
"""


import os
from typing import Optional

# Third-party imports
from supabase import create_async_client, AsyncClient

# Local application imports
from libs.shared_db.common import setup_import_paths_and_env

# Setup import paths and environment
setup_import_paths_and_env()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Cache for Supabase client to avoid creating new instances
class SupabaseClientCache:
    """Singleton class to manage Supabase client instances."""

    _instance = None
    _supabase_client: Optional[AsyncClient] = None
    _supabase_admin_client: Optional[AsyncClient] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_client(self) -> AsyncClient:
        """Get or create a cached Supabase client instance."""
        if self._supabase_client is None:
            self._supabase_client = await create_async_client(SUPABASE_URL, SUPABASE_ANON_KEY)
            print("Supabase client created and cached")
        return self._supabase_client

    async def get_admin_client(self) -> AsyncClient:
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

            self._supabase_admin_client = await create_async_client(
                SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
            )
            print("Supabase admin client created and cached")

            # Warm up the admin client to ensure proper authentication
            try:
                # Test the admin client with a simple operation
                await self._supabase_admin_client.auth.admin.list_users()
                print("Supabase admin client authenticated successfully")
            except Exception as e:
                # Surface a clearer message if the provided key is not a service-role key
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
        print("Supabase admin client cache reset")


# Global cache instance
_cache = SupabaseClientCache()


async def get_supabase_client():
    """
    Get or create a cached Supabase client instance.
    Uses caching to improve performance by reusing the same client.
    """
    return await _cache.get_client()


async def get_supabase_admin_client():
    """
    Get or create a cached Supabase admin client instance.
    Uses caching to improve performance by reusing the same client.
    """
    return await _cache.get_admin_client()


def reset_supabase_admin_client():
    """
    Reset the admin client cache to force recreation.
    Useful when the admin client gets into a corrupted state.
    """
    _cache.reset_admin_client()


async def get_fresh_supabase_admin_client():
    """
    Get a fresh admin client instance, bypassing cache.
    Useful when the cached client gets into a corrupted state.
    """
    return await _cache.get_fresh_admin_client()
