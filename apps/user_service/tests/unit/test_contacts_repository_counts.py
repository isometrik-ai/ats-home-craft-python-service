"""Unit tests for ContactsRepository contact overview query."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.schemas.enums import ClientStatus, ContactType


class _FakeConn:
    """Minimal fake asyncpg connection for repository tests."""

    def __init__(self, *, row=None):
        self.row = row
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, query, *args):
        """Record fetchrow call and return configured row."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row


@pytest.mark.asyncio
async def test_get_contact_overview_default_excludes_deleted():
    """Default overview excludes soft-deleted contacts."""
    conn = _FakeConn(
        row={"total": 26, "owners": 16, "tenants": 2, "vendors": 8},
    )
    repo = ContactsRepository(db_connection=conn)
    result = await repo.get_contact_overview(organization_id="org-1", status=None)

    assert result == {"total": 26, "owners": 16, "tenants": 2, "vendors": 8}
    query, args = conn.fetchrow_calls[0]
    assert "status <>" in query
    assert "org-1" in args
    assert ClientStatus.DELETED.value in args
    assert ContactType.OWNER.value in args
    assert ContactType.TENANT.value in args
    assert ContactType.VENDOR.value in args


@pytest.mark.asyncio
async def test_get_contact_overview_status_active():
    """Active tab overview counts only active contacts."""
    conn = _FakeConn(
        row={"total": 20, "owners": 14, "tenants": 2, "vendors": 4},
    )
    repo = ContactsRepository(db_connection=conn)
    result = await repo.get_contact_overview(
        organization_id="org-1",
        status=ClientStatus.ACTIVE.value,
    )

    assert result["total"] == 20
    query, args = conn.fetchrow_calls[0]
    assert "status =" in query
    assert ClientStatus.ACTIVE.value in args


@pytest.mark.asyncio
async def test_get_contact_overview_status_deleted():
    """Deleted tab overview counts only soft-deleted contacts."""
    conn = _FakeConn(
        row={"total": 3, "owners": 1, "tenants": 0, "vendors": 2},
    )
    repo = ContactsRepository(db_connection=conn)
    result = await repo.get_contact_overview(
        organization_id="org-1",
        status=ClientStatus.DELETED.value,
    )

    assert result["total"] == 3
    query, args = conn.fetchrow_calls[0]
    assert "status =" in query
    assert ClientStatus.DELETED.value in args


@pytest.mark.asyncio
async def test_get_contact_overview_empty_row():
    """Missing row returns zero counts."""
    conn = _FakeConn(row=None)
    repo = ContactsRepository(db_connection=conn)
    result = await repo.get_contact_overview(organization_id="org-1", status=None)
    assert result == {"total": 0, "owners": 0, "tenants": 0, "vendors": 0}
