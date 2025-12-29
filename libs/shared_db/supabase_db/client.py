"""
Supabase async client factory with caching.

Provides two cached clients:
- Anon client (RLS-enforced)
- Service client (admin/service-role)
"""

from __future__ import annotations

import os
from typing import Optional

from httpx import AsyncClient as HTTPXAsyncClient
from supabase import AsyncClient, ClientOptions, create_async_client

from libs.shared_db.common import setup_import_paths_and_env

setup_import_paths_and_env()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")


class _SupabaseCache:
    """Simple cache for Supabase clients."""

    def __init__(self) -> None:
        self.anon: AsyncClient | None = None
        self.service: AsyncClient | None = None

    def reset_service(self) -> None:
        self.service = None

    def reset_all(self) -> None:
        self.anon = None
        self.service = None


_cache = _SupabaseCache()


async def get_supabase_client() -> AsyncClient:
    """
    Get the cached anon Supabase client (RLS enforced).
    """
    if _cache.anon is None:
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be set for anon client.")
        _cache.anon = await create_async_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _cache.anon


async def get_supabase_service_client(
    user_agent: Optional[str] = None, custom_headers: Optional[dict] = None
) -> AsyncClient:
    """
    Get the cached service-role Supabase client (admin privileges).
    """
    if _cache.service is None or user_agent or custom_headers:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for service client.")

        headers = {}
        if user_agent:
            headers["User-Agent"] = user_agent
        if custom_headers:
            headers.update(custom_headers)

        http_client = HTTPXAsyncClient(headers=headers) if headers else None
        options = ClientOptions(persist_session=False) if http_client else None

        _cache.service = await create_async_client(
            SUPABASE_URL,
            SUPABASE_SERVICE_KEY,
            options=options,
        )

        # Warm-up to ensure credentials are valid
        try:
            await _cache.service.auth.admin.list_users()
        except Exception as exc:  # noqa: BLE001
            _cache.service = None
            raise RuntimeError(
                "Supabase service client warm-up failed; verify SUPABASE_SERVICE_KEY and URL."
            ) from exc

    return _cache.service


def reset_supabase_service_client() -> None:
    """Reset the cached service client."""
    _cache.reset_service()


async def get_fresh_supabase_service_client() -> AsyncClient:
    """Force-create a fresh service client, bypassing cache."""
    reset_supabase_service_client()
    return await get_supabase_service_client()
