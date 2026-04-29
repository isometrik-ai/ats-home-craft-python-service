"""Supabase client dependencies for FastAPI.

Provides:
- supabase_service: service-role client (admin operations)
- supabase_anon: anon client (RLS-enforced)
"""

from __future__ import annotations

from fastapi import Request
from supabase import AsyncClient

from libs.shared_db.supabase_db.client import (
    get_supabase_anon_with_pkce_flow,
    get_supabase_client,
    get_supabase_service_client,
    supabase_anon_with_headers,
)


async def supabase_service():
    """Yield the service-role Supabase client."""
    return await get_supabase_service_client()


async def supabase_anon():
    """Yield the anon Supabase client."""
    return await get_supabase_client()

async def supabase_anon_with_pkce_flow(request: Request) -> AsyncClient:
    """Yield the anon Supabase client with implicit flow."""
    return await get_supabase_anon_with_pkce_flow(request)


async def supabase_anon_client_with_headers(request: Request) -> AsyncClient:
    """FastAPI dependency for a per-request Supabase anon client (RLS-enforced),
    automatically including headers like User-Agent and X-Device-Signature.
    """
    return await supabase_anon_with_headers(request)
