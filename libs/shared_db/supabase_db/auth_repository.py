"""
Auth repository helpers using the Supabase Python client.

Use with the service client for admin operations on auth users.
"""

from __future__ import annotations

from typing import Any

from supabase import AsyncClient


async def create_user(
    client: AsyncClient,
    email: str,
    password: str,
    user_metadata: dict[str, Any] | None = None,
    phone: str | None = None,
    email_confirm: bool = False,
) -> dict[str, Any]:
    """
    Create a Supabase Auth user (service client required).
    """
    try:
        resp = await client.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "user_metadata": user_metadata or {},
                "phone": phone,
                "email_confirm": email_confirm,
            }
        )
        return resp.user.model_dump() if resp and resp.user else {}
    except Exception as exc:  # noqa: BLE001
        raise exc


async def get_user_by_id(client: AsyncClient, user_id: str) -> dict[str, Any]:
    """Fetch an auth user by ID."""
    try:
        resp = await client.auth.admin.get_user_by_id(user_id)
        return resp.user.model_dump() if resp and resp.user else {}
    except Exception as exc:  # noqa: BLE001
        raise exc


async def delete_user(client: AsyncClient, user_id: str) -> bool:
    """Delete an auth user by ID."""
    try:
        await client.auth.admin.delete_user(user_id)
        return True
    except Exception as exc:  # noqa: BLE001
        raise exc


async def list_users(client: AsyncClient, page: int = 1, per_page: int = 50) -> list[dict[str, Any]]:
    """List auth users with pagination."""
    try:
        resp = await client.auth.admin.list_users(page=page, per_page=per_page)
        if resp and resp.users:
            return [user.model_dump() for user in resp.users]
        return []
    except Exception as exc:  # noqa: BLE001
        raise exc
