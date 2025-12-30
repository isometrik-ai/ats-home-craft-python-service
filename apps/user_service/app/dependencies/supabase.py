"""Supabase client dependencies for FastAPI.

Provides:
- supabase_service: service-role client (admin operations)
- supabase_anon: anon client (RLS-enforced)
"""

from __future__ import annotations

from libs.shared_db.supabase_db.client import (
    get_supabase_client,
    get_supabase_service_client,
)


async def supabase_service():
    """Yield the service-role Supabase client."""
    return await get_supabase_service_client()


async def supabase_anon():
    """Yield the anon Supabase client."""
    return await get_supabase_client()
