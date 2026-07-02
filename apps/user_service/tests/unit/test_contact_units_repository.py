"""Unit tests for ContactUnitsRepository with fake asyncpg connection."""

import pytest

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.fetch_calls = []

    async def fetch(self, query, *args):
        """Record fetch call and return configured rows."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows


@pytest.mark.asyncio
async def test_list_by_contact_filters_statuses():
    """Status filter adds ANY clause to the query."""
    conn = _FakeConn(rows=[])
    repo = ContactUnitsRepository(db_connection=conn)

    await repo.list_by_contact(
        organization_id="org-1",
        contact_id="contact-1",
        statuses=["pending", "active"],
    )

    assert len(conn.fetch_calls) == 1
    query, args = conn.fetch_calls[0]
    assert "cu.contact_id = $2::uuid" in query
    assert "cu.status = ANY" in query
    assert args[0] == "org-1"
    assert args[1] == "contact-1"
    assert args[2] == ["pending", "active"]


@pytest.mark.asyncio
async def test_list_by_contact_without_status_filter():
    """Omitting statuses does not add a status filter."""
    conn = _FakeConn(rows=[])
    repo = ContactUnitsRepository(db_connection=conn)

    await repo.list_by_contact(
        organization_id="org-1",
        contact_id="contact-1",
    )

    query, args = conn.fetch_calls[0]
    assert "cu.status = ANY" not in query
    assert args == ("org-1", "contact-1")
