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
from ..common import setup_import_paths_and_env

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
        """Get or create a cached Supabase admin client instance."""
        if self._supabase_admin_client is None:
            self._supabase_admin_client = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
            print("Supabase admin client created and cached")
        return self._supabase_admin_client


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
