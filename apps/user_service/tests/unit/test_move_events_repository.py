"""Unit tests for MoveEventsRepository query building."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.move_events_repository import (
    MoveEventsRepository,
)
from apps.user_service.app.schemas.enums import MoveEventListBucket


class _FakeConn:
    """Minimal fake asyncpg connection for repository tests."""

    def __init__(self, *, rows=None, row=None, val=0):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.fetch_calls = []
        self.fetchrow_calls = []
        self.fetchval_calls = []

    async def fetch(self, query, *args):
        """Record fetch call and return configured rows."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        """Record fetchrow call and return configured row."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        """Record fetchval call and return configured scalar."""
        self.fetchval_calls.append((query.strip(), args))
        return self.val

    async def execute(self, query, *args):
        """Record execute call."""
        return None


@pytest.mark.asyncio
async def test_insert_move_event_casts_enum():
    """Insert statement casts move_type to Postgres enum."""
    conn = _FakeConn(row={"id": "move-1"})
    repo = MoveEventsRepository(db_connection=conn)
    await repo.insert(
        {
            "organization_id": "org-1",
            "project_id": "project-1",
            "unit_id": "unit-1",
            "contact_id": "contact-1",
            "contact_unit_id": "cu-1",
            "move_type": "move_in",
            "event_date": "2026-05-25",
            "fee_amount": 5000,
            "fee_currency": "INR",
            "notes": "Handover",
            "document_paths": [],
            "recorded_by_user_id": "user-1",
        }
    )
    query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO move_events" in query
    assert "::move_event_type" in query


@pytest.mark.asyncio
async def test_list_move_events_bucket_and_search_filters():
    """List adds move_type bucket and search predicates."""
    conn = _FakeConn(rows=[], val=0)
    repo = MoveEventsRepository(db_connection=conn)
    await repo.list(
        organization_id="org-1",
        bucket=MoveEventListBucket.MOVE_OUT.value,
        search="A-0101",
        page=1,
        page_size=20,
    )
    list_query, _ = conn.fetch_calls[0]
    count_query, _ = conn.fetchval_calls[0]
    assert "::move_event_type" in list_query
    assert "ILIKE" in list_query
    assert "me.deleted_at IS NULL" in count_query


@pytest.mark.asyncio
async def test_soft_delete_sets_deleted_at():
    """Soft delete updates deleted_at and returns row metadata."""
    conn = _FakeConn(
        row={
            "id": "move-1",
            "unit_id": "unit-1",
            "contact_id": "contact-1",
            "contact_unit_id": "cu-1",
            "move_type": "move_in",
            "event_date": "2026-05-25",
        }
    )
    repo = MoveEventsRepository(db_connection=conn)
    result = await repo.soft_delete(organization_id="org-1", move_event_id="move-1")
    query, _ = conn.fetchrow_calls[0]
    assert "deleted_at = now()" in query
    assert result is not None
    assert result["move_type"] == "move_in"


@pytest.mark.asyncio
async def test_get_by_id_and_latest_for_unit_contact():
    """Fetch single event and latest per unit+contact."""
    conn = _FakeConn(row={"id": "move-1", "unit_code": "A-101"})
    repo = MoveEventsRepository(db_connection=conn)

    found = await repo.get_by_id(organization_id="org-1", move_event_id="move-1")
    assert found["unit_code"] == "A-101"

    conn.row = {"id": "move-2", "move_type": "move_out", "event_date": "2026-06-01"}
    latest = await repo.get_latest_for_unit_contact(
        organization_id="org-1",
        unit_id="unit-1",
        contact_id="contact-1",
    )
    assert latest["move_type"] == "move_out"


@pytest.mark.asyncio
async def test_list_with_unit_and_project_filters():
    """List adds unit_id and project_id predicates."""
    conn = _FakeConn(rows=[], val=0)
    repo = MoveEventsRepository(db_connection=conn)

    await repo.list(
        organization_id="org-1",
        unit_id="unit-1",
        project_id="project-1",
        page=2,
        page_size=10,
    )

    count_query, _ = conn.fetchval_calls[0]
    list_query, list_args = conn.fetch_calls[0]
    assert "me.unit_id = $2::uuid" in count_query
    assert "me.project_id = $3::uuid" in count_query
    assert list_args[-2] == 10
    assert list_args[-1] == 10


@pytest.mark.asyncio
async def test_update_move_event_and_noop_paths():
    """Update patches allowed fields and re-fetches; ignores unknown fields."""
    conn = _FakeConn(
        row={"id": "move-1", "unit_code": "A-101"},
        val=1,
    )
    repo = MoveEventsRepository(db_connection=conn)

    updated = await repo.update(
        organization_id="org-1",
        move_event_id="move-1",
        update_data={
            "event_date": "2026-06-01",
            "document_paths": ["/docs/a.pdf"],
            "fee_amount": 1000,
            "move_type": "ignored",
        },
    )
    assert updated["unit_code"] == "A-101"
    update_query, _ = conn.fetchrow_calls[0]
    assert "event_date = $1::date" in update_query
    assert "document_paths = $2::text[]" in update_query
    assert "move_type" not in update_query

    conn.fetchrow_calls.clear()
    noop = await repo.update(
        organization_id="org-1",
        move_event_id="move-1",
        update_data={"move_type": "ignored"},
    )
    assert noop["id"] == "move-1"
    assert "me.id = $2::uuid" in conn.fetchrow_calls[0][0]

    conn.row = None
    missing = await repo.update(
        organization_id="org-1",
        move_event_id="missing",
        update_data={"notes": "n/a"},
    )
    assert missing is None


@pytest.mark.asyncio
async def test_contact_exists():
    """contact_exists checks active contacts in org."""
    conn = _FakeConn(val=1)
    repo = MoveEventsRepository(db_connection=conn)

    assert await repo.contact_exists(organization_id="org-1", contact_id="contact-1")

    conn.val = None
    assert not await repo.contact_exists(organization_id="org-1", contact_id="contact-1")
