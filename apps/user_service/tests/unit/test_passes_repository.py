"""Unit tests for PassesRepository query building."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.passes_repository import PassesRepository
from apps.user_service.app.schemas.enums import (
    PassDisplayStatus,
    PassListBucket,
    PassStatus,
    PassValidityType,
)


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


@pytest.mark.asyncio
async def test_insert_pass_casts_enums():
    """Insert statement casts enum columns to Postgres types."""
    conn = _FakeConn(row={"id": "pass-1"})
    repo = PassesRepository(db_connection=conn)
    await repo.insert(
        {
            "organization_id": "org-1",
            "project_id": "project-1",
            "unit_id": "unit-1",
            "host_contact_id": "contact-1",
            "pass_type": "guest",
            "guest_name": "Guest",
            "valid_from": "2026-07-10T09:00:00Z",
            "valid_until": "2026-07-10T21:00:00Z",
            "validity_type": "one_time",
            "code": "4821",
            "created_by_contact_id": "contact-1",
        }
    )
    query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO passes" in query
    assert "::pass_type" in query
    assert "::pass_validity_type" in query
    assert "::pass_status" in query


@pytest.mark.asyncio
async def test_list_by_contact_active_bucket_filter():
    """Active bucket adds validity window predicates to list/count queries."""
    conn = _FakeConn(rows=[], val=0)
    repo = PassesRepository(db_connection=conn)
    await repo.list_by_contact(
        organization_id="org-1",
        host_contact_id="contact-1",
        bucket=PassListBucket.ACTIVE.value,
        page=1,
        page_size=20,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "p.valid_from <= now()" in count_query
    assert "p.valid_until >= now()" in count_query
    assert PassStatus.ACTIVE.value in count_args


@pytest.mark.asyncio
async def test_list_display_status_cancelled():
    """Cancelled display_status filters by cancelled pass status."""
    conn = _FakeConn(rows=[], val=0)
    repo = PassesRepository(db_connection=conn)
    await repo.list_by_contact(
        organization_id="org-1",
        host_contact_id="contact-1",
        display_status=PassDisplayStatus.CANCELLED.value,
        page=1,
        page_size=20,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "p.status = $3::pass_status" in count_query
    assert count_args[2] == PassStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_list_display_status_used():
    """Used display_status matches completed or one-time entry passes."""
    conn = _FakeConn(rows=[], val=0)
    repo = PassesRepository(db_connection=conn)
    await repo.list_by_contact(
        organization_id="org-1",
        host_contact_id="contact-1",
        display_status=PassDisplayStatus.USED.value,
        page=1,
        page_size=20,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "p.entry_count > 0" in count_query
    assert PassStatus.COMPLETED.value in count_args
    assert PassValidityType.ONE_TIME.value in count_args


@pytest.mark.asyncio
async def test_list_display_status_upcoming():
    """Upcoming display_status filters future validity windows."""
    conn = _FakeConn(rows=[], val=0)
    repo = PassesRepository(db_connection=conn)
    await repo.list_by_contact(
        organization_id="org-1",
        host_contact_id="contact-1",
        display_status=PassDisplayStatus.UPCOMING.value,
        page=1,
        page_size=20,
    )
    count_query, _ = conn.fetchval_calls[0]
    assert "p.valid_from > now()" in count_query


@pytest.mark.asyncio
async def test_code_exists_active_lookup():
    """Active code uniqueness check filters by organization and active status."""
    conn = _FakeConn(val=1)
    repo = PassesRepository(db_connection=conn)
    exists = await repo.code_exists_active(organization_id="org-1", code="4821")
    assert exists is True
    query, args = conn.fetchval_calls[0]
    assert "status = $3::pass_status" in query
    assert args == ("org-1", "4821", PassStatus.ACTIVE.value)


@pytest.mark.asyncio
async def test_get_by_code_active_lookup():
    """Active code lookup filters by organization and active status."""
    conn = _FakeConn(row={"id": "pass-1", "code": "4821"})
    repo = PassesRepository(db_connection=conn)
    row = await repo.get_by_code(organization_id="org-1", code="4821")
    assert row is not None
    query, args = conn.fetchrow_calls[0]
    assert "p.code = $2" in query
    assert "p.status = $3::pass_status" in query
    assert args == ("org-1", "4821", PassStatus.ACTIVE.value)


@pytest.mark.asyncio
async def test_increment_entry_count():
    """Increment entry_count updates the pass row."""
    conn = _FakeConn(row={"id": "pass-1", "entry_count": 2})
    repo = PassesRepository(db_connection=conn)
    row = await repo.increment_entry_count(organization_id="org-1", pass_id="pass-1")
    assert row["entry_count"] == 2
    query, _ = conn.fetchrow_calls[0]
    assert "entry_count = entry_count + 1" in query


@pytest.mark.asyncio
async def test_complete_pass():
    """Complete sets pass status to completed."""
    conn = _FakeConn(row={"id": "pass-1", "status": PassStatus.COMPLETED.value})
    repo = PassesRepository(db_connection=conn)
    row = await repo.complete(organization_id="org-1", pass_id="pass-1")
    assert row["status"] == PassStatus.COMPLETED.value
    query, args = conn.fetchrow_calls[0]
    assert "status = $3::pass_status" in query
    assert args[-1] == PassStatus.COMPLETED.value
