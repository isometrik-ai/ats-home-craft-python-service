"""Supabase async client factory with caching.

Provides:
- Anon client factory (RLS-enforced, not cached)
- Cached service client (admin/service-role)
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
        # NOTE: We intentionally avoid caching anon clients.
        #
        # anon clients are used for user-auth flows (e.g. verify_otp/sign-in) which
        # mutate client auth state (Authorization header + in-memory session). If
        # cached globally, one request can "poison" later requests.
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
    """Create an anon Supabase client (RLS enforced).

    IMPORTANT: This is NOT cached.
    `supabase-py` auth flows like `verify_otp()` mutate the passed client instance
    (session + Authorization header). Returning a fresh client avoids cross-request
    session leakage and accidental privilege downgrades.
    """
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be set for anon client.")

    options = ClientOptions(persist_session=False)
    return await create_async_client(SUPABASE_URL, SUPABASE_ANON_KEY, options=options)


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

        # Force service-role Authorization explicitly.
        #
        # In supabase-py, if ClientOptions.headers does NOT include Authorization,
        # AsyncClient.create() may attempt to load an existing auth session and set
        # Authorization: Bearer <user_access_token>, even when the client was
        # created with the service role key.
        #
        # By setting Authorization here, we guarantee admin calls always use the
        # service role bearer token and cannot be affected by any user auth flows.
        options = ClientOptions(
            persist_session=False,
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            },
        )

        _cache.service = await create_async_client(
            SUPABASE_URL, SUPABASE_SERVICE_KEY, options=options
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
