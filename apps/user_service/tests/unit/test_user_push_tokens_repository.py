"""Unit tests for UserPushTokensRepository."""

from datetime import datetime, timezone

import pytest

from apps.user_service.app.db.repositories.user_push_tokens_repository import (
    UserPushTokensRepository,
)


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self):
        self.fetchrow_calls = []
        self.fetch_calls = []
        self.execute_calls = []
        self.fetchrow_result = None
        self.fetch_result = []
        self.execute_result = "DELETE 2"

    async def fetch(self, query, *args):
        """Record fetch calls."""
        self.fetch_calls.append((query.strip(), args))
        return self.fetch_result

    async def fetchrow(self, query, *args):
        """Record fetchrow calls."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.fetchrow_result

    async def execute(self, query, *args):
        """Record execute calls."""
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


@pytest.mark.asyncio
async def test_upsert_device_uses_conflict_on_device_id():
    """upsert_device issues INSERT ... ON CONFLICT (device_id) DO UPDATE."""
    conn = _FakeConn()
    conn.fetchrow_result = {
        "device_id": "device-xyz",
        "platform": "ios",
        "updated_at": datetime(2026, 5, 29, tzinfo=timezone.utc),
    }
    repo = UserPushTokensRepository(db_connection=conn)

    result = await repo.upsert_device(
        device_id="device-xyz",
        organization_id="org-1",
        user_id="user-1",
        push_token="token-abc",
        platform="ios",
        app_version="1.0.0",
    )

    assert result is not None
    assert result["device_id"] == "device-xyz"
    assert len(conn.fetchrow_calls) == 1
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO user_push_tokens" in query
    assert "ON CONFLICT (device_id) DO UPDATE" in query
    assert args == (
        "device-xyz",
        "org-1",
        "user-1",
        "token-abc",
        "ios",
        "fcm",
        "1.0.0",
    )


@pytest.mark.asyncio
async def test_upsert_device_handoff_updates_owner_fields():
    """Second upsert on same device_id passes new user id (device handoff)."""
    conn = _FakeConn()
    conn.fetchrow_result = {"device_id": "device-xyz", "updated_at": datetime.now(timezone.utc)}
    repo = UserPushTokensRepository(db_connection=conn)

    await repo.upsert_device(
        device_id="device-xyz",
        organization_id="org-1",
        user_id="user-2",
        push_token="token-abc",
        platform="android",
    )

    _, args = conn.fetchrow_calls[0]
    assert args[2] == "user-2"


@pytest.mark.asyncio
async def test_delete_by_device_and_user_scoped():
    """delete_by_device_and_user scopes DELETE by device_id and user_id."""
    conn = _FakeConn()
    conn.fetchrow_result = {"id": "row-1"}
    repo = UserPushTokensRepository(db_connection=conn)

    removed = await repo.delete_by_device_and_user(device_id="device-xyz", user_id="user-1")

    assert removed is True
    query, args = conn.fetchrow_calls[0]
    assert "DELETE FROM user_push_tokens" in query
    assert "device_id = $1" in query
    assert "user_id = $2" in query
    assert args == ("device-xyz", "user-1")


@pytest.mark.asyncio
async def test_delete_device_user_not_found():
    """delete_by_device_and_user returns False when no row is deleted."""
    conn = _FakeConn()
    conn.fetchrow_result = None
    repo = UserPushTokensRepository(db_connection=conn)

    removed = await repo.delete_by_device_and_user(device_id="missing", user_id="user-1")

    assert removed is False


@pytest.mark.asyncio
async def test_delete_by_user_scoped():
    """delete_by_user scopes DELETE by user_id."""
    conn = _FakeConn()
    repo = UserPushTokensRepository(db_connection=conn)

    deleted_count = await repo.delete_by_user(user_id="user-1")

    assert deleted_count == 2
    query, args = conn.execute_calls[0]
    assert "DELETE FROM user_push_tokens" in query
    assert "user_id = $1" in query
    assert args == ("user-1",)


@pytest.mark.asyncio
async def test_list_push_tokens_for_user_returns_distinct_tokens():
    """list_push_tokens_for_user returns ordered distinct push tokens."""
    conn = _FakeConn()
    conn.fetch_result = [
        {"push_token": "token-a"},
        {"push_token": "token-b"},
    ]
    repo = UserPushTokensRepository(db_connection=conn)

    tokens = await repo.list_push_tokens_for_user(
        organization_id="org-1",
        user_id="user-1",
    )

    assert tokens == ["token-a", "token-b"]
    query, args = conn.fetch_calls[0]
    assert "SELECT DISTINCT push_token" in query
    assert "FROM user_push_tokens" in query
    assert args == ("org-1", "user-1")


@pytest.mark.asyncio
async def test_list_push_tokens_for_user_empty_scope():
    """list_push_tokens_for_user returns empty list for missing ids."""
    conn = _FakeConn()
    repo = UserPushTokensRepository(db_connection=conn)

    assert await repo.list_push_tokens_for_user(organization_id="", user_id="user-1") == []
    assert await repo.list_push_tokens_for_user(organization_id="org-1", user_id="") == []
