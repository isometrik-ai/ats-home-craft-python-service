"""Supabase async client factory with caching.

Provides two cached clients:
- Anon client (RLS-enforced)
- Service client (admin/service-role)
"""

from __future__ import annotations

from fastapi import Request
from supabase import AsyncClient, ClientOptions, create_async_client

from libs.shared_config.app_settings import shared_settings

SUPABASE_URL = shared_settings.supabase.url
SUPABASE_ANON_KEY = shared_settings.supabase.anon_key
SUPABASE_SERVICE_KEY = shared_settings.supabase.service_key


class _SupabaseCache:
    """Simple cache for Supabase clients."""

    def __init__(self) -> None:
        self.anon: AsyncClient | None = None
        self.service: AsyncClient | None = None

    def reset_service(self) -> None:
        """Reset the cached service client."""
        self.service = None

    def reset_all(self) -> None:
        """Reset all cached clients."""
        self.anon = None
        self.service = None


_cache = _SupabaseCache()


async def get_supabase_client() -> AsyncClient:
    """Get the cached anon Supabase client (RLS enforced)."""
    if _cache.anon is None:
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be set for anon client.")
        _cache.anon = await create_async_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _cache.anon


async def supabase_anon_with_headers(
    request: Request,
) -> AsyncClient:
    """Create a per-request anon Supabase client with custom headers.
    NOT cached.
    """
    headers: dict[str, str] = {}

    user_agent = request.headers.get("User-Agent")
    device_signature = request.headers.get("X-Device-Signature")

    if user_agent:
        headers["User-Agent"] = user_agent
    if device_signature:
        headers["X-Device-Signature"] = device_signature

    options = ClientOptions(
        persist_session=False,
        headers=headers,
    )

    return await create_async_client(
        SUPABASE_URL,
        SUPABASE_ANON_KEY,
        options=options,
    )


async def get_supabase_service_client() -> AsyncClient:
    """Get the cached service-role Supabase client (admin privileges)."""
    if _cache.service is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for service client."
            )

        _cache.service = await create_async_client(
            SUPABASE_URL,
            SUPABASE_SERVICE_KEY,
            options=ClientOptions(persist_session=False),
        )

        try:
            await _cache.service.auth.admin.list_users()
        except Exception as exc:
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
