"""Supabase async client factory with caching.

Provides two cached clients:
- Anon client (RLS-enforced)
- Service client (admin/service-role)
"""

from __future__ import annotations

from httpx import AsyncClient as HTTPXAsyncClient
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


async def get_supabase_service_client(
    user_agent: str | None = None, custom_headers: dict | None = None
) -> AsyncClient:
    """Get the cached service-role Supabase client (admin privileges).
    Args:
        user_agent: Optional User-Agent string
        custom_headers: Optional dict of additional custom headers (e.g., X-Device-Signature)
    Returns:
        AsyncClient: The cached service-role Supabase client
    Raises:
        RuntimeError: If SUPABASE_URL or SUPABASE_SERVICE_KEY is not set
        Exception: If the service client warm-up fails
    """
    if _cache.service is None or user_agent or custom_headers:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for service client."
            )

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
