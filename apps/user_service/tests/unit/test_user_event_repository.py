"""Unit tests for UserEventRepository."""

import pytest

from apps.user_service.app.db.repositories.user_event_repository import (
    UserEventRepository,
)


@pytest.mark.asyncio
async def test_get_user_event_invalid_columns_filtered_out():
    """Invalid select_columns are ignored; only allowlisted columns appear in the query."""
    sent_queries = []

    async def capture_fetchrow(_self, query, *_args):
        sent_queries.append(query)
        return None

    class MockConn:
        """Mock connection for testing."""

        fetchrow = capture_fetchrow

    repo = UserEventRepository(db_connection=MockConn())
    await repo.get_user_event_by_user_id("user-1", select_columns=["status", "evil", "id"])

    assert len(sent_queries) == 1
    query = sent_queries[0]
    assert "status" in query and "id" in query
    assert "evil" not in query


@pytest.mark.asyncio
async def test_get_user_event_by_user_id_all_columns():
    """When select_columns is omitted, query selects all columns."""
    sent_queries = []

    async def capture_fetchrow(_self, query, *_args):
        sent_queries.append(query)
        return {"id": "evt-1", "user_id": "user-1", "status": "pending"}

    class MockConn:
        fetchrow = capture_fetchrow

    repo = UserEventRepository(db_connection=MockConn())
    row = await repo.get_user_event_by_user_id("user-1")

    assert row["status"] == "pending"
    assert "SELECT *" in sent_queries[0]


@pytest.mark.asyncio
async def test_update_status_by_user_id():
    """update_status_by_user_id issues UPDATE with status value."""
    execute_calls = []

    async def capture_execute(_self, query, status, user_id):
        execute_calls.append((query.strip(), status, user_id))

    class MockConn:
        execute = capture_execute

    from apps.user_service.app.schemas.enums import UserEventStatus

    repo = UserEventRepository(db_connection=MockConn())
    await repo.update_status_by_user_id("user-1", UserEventStatus.COMPLETED)

    assert len(execute_calls) == 1
    query, status, user_id = execute_calls[0]
    assert "UPDATE user_events" in query
    assert status == UserEventStatus.COMPLETED.value
    assert user_id == "user-1"
