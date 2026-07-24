"""Unit tests for super admin utility helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from libs.shared_utils.super_admin_utils import (
    SuperAdminRole,
    get_system_super_admin_emails,
    is_system_super_admin,
)


@pytest.mark.asyncio
async def test_is_system_super_admin_returns_true_for_matching_role() -> None:
    """Users with system_super_admin role should be recognized."""
    current_user = {"app_metadata": {"role": SuperAdminRole.SYSTEM_SUPER_ADMIN.value}}

    assert await is_system_super_admin(current_user) is True


@pytest.mark.asyncio
async def test_is_system_super_admin_returns_false_without_role() -> None:
    """Users without the super admin role should not match."""
    assert await is_system_super_admin({}) is False
    assert await is_system_super_admin({"app_metadata": {"role": "member"}}) is False
    assert await is_system_super_admin({"app_metadata": {}}) is False


@pytest.mark.asyncio
async def test_get_system_super_admin_emails_returns_non_empty_emails() -> None:
    """Query results should collect non-null email addresses."""
    db_connection = AsyncMock()
    db_connection.fetch = AsyncMock(
        return_value=[
            {"email": "admin@example.com"},
            {"email": None},
            {"email": "ops@example.com"},
        ]
    )

    emails = await get_system_super_admin_emails(db_connection)

    assert emails == ["admin@example.com", "ops@example.com"]
    db_connection.fetch.assert_awaited_once()
    query = db_connection.fetch.await_args.args[0]
    assert SuperAdminRole.SYSTEM_SUPER_ADMIN.value in query
    assert "auth.users" in query


@pytest.mark.asyncio
async def test_get_system_super_admin_emails_returns_empty_list() -> None:
    """No matching rows should yield an empty list."""
    db_connection = AsyncMock()
    db_connection.fetch = AsyncMock(return_value=[])

    emails = await get_system_super_admin_emails(db_connection)

    assert emails == []
