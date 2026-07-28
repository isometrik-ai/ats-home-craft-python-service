"""Unit tests for PassEventsRepository query building with fake connection."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.app.db.repositories.pass_events_repository import (
    PassEventsRepository,
)
from apps.user_service.app.schemas.enums import PassEventType

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PASS_ID = "660e8400-e29b-41d4-a716-446655440001"
EVENT_ID = "770e8400-e29b-41d4-a716-446655440002"
GATE_ID = "880e8400-e29b-41d4-a716-446655440003"
USER_ID = "990e8400-e29b-41d4-a716-446655440004"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None):
        self.rows = rows or []
        self.row = row
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row


@pytest.mark.asyncio
async def test_insert_event_casts_enums_and_returns_row():
    """Insert appends a pass timeline event with enum casts."""
    conn = _FakeConn(
        row={
            "id": EVENT_ID,
            "pass_id": PASS_ID,
            "event_type": PassEventType.CHECKED_IN.value,
            "gate_id": GATE_ID,
            "actor_type": "staff",
            "actor_user_id": USER_ID,
            "actor_label": "Gate A",
            "occurred_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "notes": "Entry",
            "metadata": {},
            "entry_method": "qr",
            "access_status": "granted",
        }
    )
    repo = PassEventsRepository(db_connection=conn)

    inserted = await repo.insert_event(
        {
            "organization_id": ORG_ID,
            "pass_id": PASS_ID,
            "event_type": PassEventType.CHECKED_IN.value,
            "gate_id": GATE_ID,
            "actor_type": "staff",
            "actor_user_id": USER_ID,
            "actor_label": "Gate A",
            "occurred_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "notes": "Entry",
            "metadata": {"lane": 1},
            "entry_method": "qr",
            "access_status": "granted",
        }
    )

    assert inserted["event_type"] == PassEventType.CHECKED_IN.value
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO pass_events" in query
    assert "::pass_event_type" in query
    assert "::pass_actor_type" in query
    assert args[0] == ORG_ID
    assert args[1] == PASS_ID


@pytest.mark.asyncio
async def test_list_by_pass_returns_events():
    """List timeline events scoped to org and pass."""
    conn = _FakeConn(
        rows=[
            {"id": EVENT_ID, "pass_id": PASS_ID, "event_type": PassEventType.CREATED.value},
            {"id": "e2", "pass_id": PASS_ID, "event_type": PassEventType.CHECKED_IN.value},
        ]
    )
    repo = PassEventsRepository(db_connection=conn)

    events = await repo.list_by_pass(organization_id=ORG_ID, pass_id=PASS_ID)

    assert len(events) == 2
    query, args = conn.fetch_calls[0]
    assert "FROM pass_events" in query
    assert "ORDER BY occurred_at ASC" in query
    assert args == (ORG_ID, PASS_ID)


@pytest.mark.asyncio
async def test_list_by_pass_empty():
    """Empty pass timeline returns no rows."""
    conn = _FakeConn(rows=[])
    repo = PassEventsRepository(db_connection=conn)

    events = await repo.list_by_pass(organization_id=ORG_ID, pass_id=PASS_ID)

    assert events == []


@pytest.mark.asyncio
async def test_latest_event_by_type():
    """Latest event of a type returns most recent row."""
    conn = _FakeConn(row={"id": EVENT_ID, "event_type": PassEventType.CHECKED_IN.value})
    repo = PassEventsRepository(db_connection=conn)

    latest = await repo.latest_event_by_type(
        organization_id=ORG_ID,
        pass_id=PASS_ID,
        event_type=PassEventType.CHECKED_IN.value,
    )

    assert latest is not None
    assert latest["event_type"] == PassEventType.CHECKED_IN.value
    query, _ = conn.fetchrow_calls[0]
    assert "::pass_event_type" in query
    assert "ORDER BY occurred_at DESC" in query


@pytest.mark.asyncio
async def test_latest_event_by_type_not_found():
    """Missing event type returns None."""
    conn = _FakeConn(row=None)
    repo = PassEventsRepository(db_connection=conn)

    latest = await repo.latest_event_by_type(
        organization_id=ORG_ID,
        pass_id=PASS_ID,
        event_type=PassEventType.CHECKED_OUT.value,
    )

    assert latest is None


@pytest.mark.asyncio
async def test_has_open_check_in_no_check_in():
    """No check-in means no open session."""
    conn = _FakeConn(row={"last_check_in": None, "last_check_out": None})
    repo = PassEventsRepository(db_connection=conn)

    assert not await repo.has_open_check_in(organization_id=ORG_ID, pass_id=PASS_ID)


@pytest.mark.asyncio
async def test_has_open_check_in_no_check_out():
    """Check-in without check-out is open."""
    check_in = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    conn = _FakeConn(row={"last_check_in": check_in, "last_check_out": None})
    repo = PassEventsRepository(db_connection=conn)

    assert await repo.has_open_check_in(organization_id=ORG_ID, pass_id=PASS_ID)
    query, args = conn.fetchrow_calls[0]
    assert PassEventType.CHECKED_IN.value in args
    assert PassEventType.CHECKED_OUT.value in args


@pytest.mark.asyncio
async def test_has_open_check_in_after_check_out():
    """Check-in after check-out means session is open."""
    check_in = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    check_out = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)
    conn = _FakeConn(row={"last_check_in": check_in, "last_check_out": check_out})
    repo = PassEventsRepository(db_connection=conn)

    assert await repo.has_open_check_in(organization_id=ORG_ID, pass_id=PASS_ID)


@pytest.mark.asyncio
async def test_has_open_check_in_closed():
    """Check-out after check-in closes the session."""
    check_in = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    check_out = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)
    conn = _FakeConn(row={"last_check_in": check_in, "last_check_out": check_out})
    repo = PassEventsRepository(db_connection=conn)

    assert not await repo.has_open_check_in(organization_id=ORG_ID, pass_id=PASS_ID)


@pytest.mark.asyncio
async def test_has_open_check_in_null_row():
    """Missing aggregate row is treated as no open check-in."""
    conn = _FakeConn(row=None)
    repo = PassEventsRepository(db_connection=conn)

    assert not await repo.has_open_check_in(organization_id=ORG_ID, pass_id=PASS_ID)
