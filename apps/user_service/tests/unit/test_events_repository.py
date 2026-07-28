"""Unit tests for EventsRepository with fake connection."""

from __future__ import annotations

import asyncpg
import pytest

from apps.user_service.app.db.repositories.events_repository import EventsRepository

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
EVENT_ID = "660e8400-e29b-41d4-a716-446655440001"
AGGREGATE_ID = "770e8400-e29b-41d4-a716-446655440002"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, execute_raises=None):
        self.rows = rows or []
        self.row = row
        self.execute_raises = execute_raises
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []
        self.executemany_calls: list[tuple[str, list]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        return self.row

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))
        if self.execute_raises:
            raise self.execute_raises
        return "UPDATE 1"

    async def executemany(self, query, values):
        self.executemany_calls.append((query.strip(), values))


@pytest.mark.asyncio
async def test_create_event():
    """Create event inserts row with jsonb payload."""
    conn = _FakeConn()
    repo = EventsRepository(db_connection=conn)

    await repo.create_event(
        event_id=EVENT_ID,
        event_type="contact.created",
        aggregate_id=AGGREGATE_ID,
        organization_id=ORG_ID,
        topic="crm.events",
        payload={"name": "Jane"},
        status="pending",
    )

    query, args = conn.execute_calls[0]
    assert "INSERT INTO events" in query
    assert args[0] == EVENT_ID
    assert args[4] == "crm.events"
    assert '"name": "Jane"' in args[5]


@pytest.mark.asyncio
async def test_create_event_default_status():
    """Create event defaults status to pending."""
    conn = _FakeConn()
    repo = EventsRepository(db_connection=conn)

    await repo.create_event(
        event_id=EVENT_ID,
        event_type="contact.updated",
        aggregate_id=AGGREGATE_ID,
        organization_id=ORG_ID,
        topic="crm.events",
        payload={},
    )

    assert conn.execute_calls[0][1][6] == "pending"


@pytest.mark.asyncio
async def test_bulk_create_events_empty_noop():
    """Bulk create with empty list skips executemany."""
    conn = _FakeConn()
    repo = EventsRepository(db_connection=conn)

    await repo.bulk_create_events(events=[])

    assert conn.executemany_calls == []


@pytest.mark.asyncio
async def test_bulk_create_events():
    """Bulk create inserts multiple rows."""
    conn = _FakeConn()
    repo = EventsRepository(db_connection=conn)

    await repo.bulk_create_events(
        events=[
            {
                "event_id": EVENT_ID,
                "event_type": "contact.created",
                "aggregate_id": AGGREGATE_ID,
                "organization_id": ORG_ID,
                "topic": "crm.events",
                "payload": {"a": 1},
                "status": "pending",
            },
            {
                "event_id": "e2",
                "event_type": "contact.updated",
                "aggregate_id": AGGREGATE_ID,
                "organization_id": ORG_ID,
                "topic": "crm.events",
                "payload": {"b": 2},
                "status": "published",
            },
        ]
    )

    query, values = conn.executemany_calls[0]
    assert "INSERT INTO events" in query
    assert len(values) == 2
    assert values[0][0] == EVENT_ID
    assert values[1][6] == "published"


@pytest.mark.asyncio
async def test_update_event_status():
    """Update event status without publishing timestamp."""
    conn = _FakeConn()
    repo = EventsRepository(db_connection=conn)

    await repo.update_event_status(event_id=EVENT_ID, status="published")

    query, args = conn.execute_calls[0]
    assert "UPDATE events" in query
    assert args == (EVENT_ID, "published", False)


@pytest.mark.asyncio
async def test_update_event_status_mark_published_at():
    """Update event status can set published_at."""
    conn = _FakeConn()
    repo = EventsRepository(db_connection=conn)

    await repo.update_event_status(
        event_id=EVENT_ID,
        status="published",
        mark_published_at=True,
    )

    assert conn.execute_calls[0][1][2] is True
    assert "published_at = CASE" in conn.execute_calls[0][0]


@pytest.mark.asyncio
async def test_fetch_crm_events_for_graphiti_replay_default_order():
    """Replay query orders chronologically by default."""
    conn = _FakeConn(
        rows=[
            {
                "event_id": EVENT_ID,
                "event_type": "contact.created",
                "aggregate_id": AGGREGATE_ID,
                "organization_id": ORG_ID,
                "payload": {"occurred_at": "2026-01-01T00:00:00Z"},
                "status": "published",
            }
        ]
    )
    repo = EventsRepository(db_connection=conn)

    events = await repo.fetch_crm_events_for_graphiti_replay(
        topic="crm.events",
        organization_id=ORG_ID,
        statuses=["published"],
        since_occurred_at="2026-01-01T00:00:00Z",
        limit=100,
    )

    assert len(events) == 1
    query, args = conn.fetch_calls[0]
    assert "DISTINCT ON" not in query
    assert "event_type NOT LIKE 'email.%'" in query
    assert args == ("crm.events", ORG_ID, ["published"], "2026-01-01T00:00:00Z", 100)


@pytest.mark.asyncio
async def test_fetch_crm_events_latest_per_aggregate():
    """Latest-per-aggregate mode uses DISTINCT ON ordering."""
    conn = _FakeConn(rows=[])
    repo = EventsRepository(db_connection=conn)

    await repo.fetch_crm_events_for_graphiti_replay(
        topic="crm.events",
        latest_per_aggregate=True,
    )

    query, _ = conn.fetch_calls[0]
    assert "DISTINCT ON (organization_id, aggregate_id)" in query
    assert "DESC NULLS LAST" in query


@pytest.mark.asyncio
async def test_fetch_crm_events_empty():
    """Replay with no rows returns empty list."""
    conn = _FakeConn(rows=[])
    repo = EventsRepository(db_connection=conn)

    events = await repo.fetch_crm_events_for_graphiti_replay(topic="crm.events")

    assert events == []


@pytest.mark.asyncio
async def test_mark_graphiti_synced():
    """Mark graphiti synced updates timestamp."""
    conn = _FakeConn()
    repo = EventsRepository(db_connection=conn)

    await repo.mark_graphiti_synced(event_id=EVENT_ID)

    query, args = conn.execute_calls[0]
    assert "graphiti_synced_at" in query
    assert args == (EVENT_ID,)


@pytest.mark.asyncio
async def test_mark_graphiti_synced_missing_column_swallowed():
    """Missing graphiti_synced_at column is logged and swallowed."""
    conn = _FakeConn(execute_raises=asyncpg.UndefinedColumnError("column missing"))
    repo = EventsRepository(db_connection=conn)

    await repo.mark_graphiti_synced(event_id=EVENT_ID)

    assert len(conn.execute_calls) == 1
