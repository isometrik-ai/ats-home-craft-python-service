"""Unit tests for UserEventRepository."""

import pytest

from apps.user_service.app.db.repositories.user_event_repository import (
    USER_EVENTS_ALLOWED_COLUMNS,
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
async def test_get_user_event_when_valid_columns_allowed():
    """Valid column names are accepted (allowlist)."""
    allowed = list(USER_EVENTS_ALLOWED_COLUMNS)
    assert "status" in allowed
    assert "id" in allowed
