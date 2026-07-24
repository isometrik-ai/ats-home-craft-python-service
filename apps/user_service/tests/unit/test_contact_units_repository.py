"""Unit tests for ContactUnitsRepository with fake asyncpg connection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.schemas.enums import ContactUnitStatus

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "660e8400-e29b-41d4-a716-446655440001"
UNIT_ID = "770e8400-e29b-41d4-a716-446655440002"
CU_ID = "880e8400-e29b-41d4-a716-446655440003"
PROJECT_ID = "990e8400-e29b-41d4-a716-446655440004"


def _async_mock_conn(*, rows=None, row=None, val=None, execute_result=None):
    """Build asyncpg-like connection mock using AsyncMock."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetchval = AsyncMock(return_value=val)
    conn.execute = AsyncMock(return_value=execute_result)
    txn = MagicMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn)
    return conn


def _sql_args(mock_method):
    """Return (query, param_tuple) from an AsyncMock DB call."""
    parts = mock_method.await_args.args
    return parts[0], parts[1:]


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, val=None, execute_result=None):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.execute_result = execute_result
        self.fetch_calls = []
        self.fetchrow_calls = []
        self.fetchval_calls = []
        self.execute_calls = []
        self._txn_entered = False

    def transaction(self):
        """Return self as async context manager for transactions."""
        return self

    async def __aenter__(self):
        """Enter transaction context."""
        self._txn_entered = True
        return self

    async def __aexit__(self, *_args):
        """Exit transaction context."""
        return None

    async def fetch(self, query, *args):
        """Record fetch call and return configured rows."""
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        """Record fetchrow call and return configured row."""
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        """Record fetchval call and return configured value."""
        self.fetchval_calls.append((query.strip(), args))
        return self.val

    async def execute(self, query, *args):
        """Record execute call."""
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


@pytest.mark.asyncio
async def test_list_by_contact_filters_statuses():
    """Status filter adds ANY clause to the query."""
    conn = _FakeConn(rows=[])
    repo = ContactUnitsRepository(db_connection=conn)

    await repo.list_by_contact(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        statuses=["pending", "active"],
    )

    assert len(conn.fetch_calls) == 1
    query, args = conn.fetch_calls[0]
    assert "cu.contact_id = $2::uuid" in query
    assert "cu.status = ANY" in query
    assert args[0] == ORG_ID
    assert args[1] == CONTACT_ID
    assert args[2] == ["pending", "active"]


@pytest.mark.asyncio
async def test_list_by_contact_without_status_filter():
    """Omitting statuses does not add a status filter."""
    conn = _FakeConn(rows=[])
    repo = ContactUnitsRepository(db_connection=conn)

    await repo.list_by_contact(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
    )

    query, args = conn.fetch_calls[0]
    assert "cu.status = ANY" not in query
    assert args == (ORG_ID, CONTACT_ID)


@pytest.mark.asyncio
async def test_get_by_id():
    """get_by_id returns dict row."""
    conn = _FakeConn(row={"id": CU_ID})
    repo = ContactUnitsRepository(db_connection=conn)

    row = await repo.get_by_id(organization_id=ORG_ID, contact_unit_id=CU_ID)

    assert row["id"] == CU_ID
    query, args = conn.fetchrow_calls[0]
    assert "cu.id = $2::uuid" in query


@pytest.mark.asyncio
async def test_get_owned_by_contact():
    """Owned lookup scopes by contact_id."""
    conn = _FakeConn(row={"id": CU_ID})
    repo = ContactUnitsRepository(db_connection=conn)

    row = await repo.get_owned_by_contact(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        contact_unit_id=CU_ID,
    )

    assert row["id"] == CU_ID
    query, args = conn.fetchrow_calls[0]
    assert "cu.contact_id = $3::uuid" in query


@pytest.mark.asyncio
async def test_contact_has_active_unit():
    """Active unit check returns bool from fetchval."""
    conn = _FakeConn(val=1)
    repo = ContactUnitsRepository(db_connection=conn)

    assert (
        await repo.contact_has_active_unit(
            organization_id=ORG_ID,
            contact_id=CONTACT_ID,
            unit_id=UNIT_ID,
        )
        is True
    )


@pytest.mark.asyncio
async def test_get_by_unit_and_contact():
    """Unit+contact lookup uses both ids."""
    conn = _FakeConn(row={"id": CU_ID})
    repo = ContactUnitsRepository(db_connection=conn)

    row = await repo.get_by_unit_and_contact(
        organization_id=ORG_ID,
        unit_id=UNIT_ID,
        contact_id=CONTACT_ID,
    )

    assert row["id"] == CU_ID
    query, _ = conn.fetchrow_calls[0]
    assert "cu.unit_id = $2::uuid" in query


@pytest.mark.asyncio
async def test_sync_move_in():
    """Move-in activates link and sets activated_at."""
    conn = _FakeConn(row={"id": CU_ID, "status": ContactUnitStatus.ACTIVE.value})
    repo = ContactUnitsRepository(db_connection=conn)

    row = await repo.sync_move_in(
        organization_id=ORG_ID,
        contact_unit_id=CU_ID,
        event_date="2026-01-01",
    )

    assert row["status"] == ContactUnitStatus.ACTIVE.value
    query, _ = conn.fetchrow_calls[0]
    assert "UPDATE contact_units" in query


@pytest.mark.asyncio
async def test_sync_move_out():
    """Move-out sets moved_out status."""
    conn = _FakeConn(row={"id": CU_ID, "status": ContactUnitStatus.MOVED_OUT.value})
    repo = ContactUnitsRepository(db_connection=conn)

    row = await repo.sync_move_out(
        organization_id=ORG_ID,
        contact_unit_id=CU_ID,
        event_date="2026-01-01",
    )

    assert row["status"] == ContactUnitStatus.MOVED_OUT.value


@pytest.mark.asyncio
async def test_count_active_units():
    """Count active units returns int."""
    conn = _async_mock_conn(val=3)
    repo = ContactUnitsRepository(db_connection=conn)

    count = await repo.count_active_units(organization_id=ORG_ID, contact_id=CONTACT_ID)

    assert count == 3


@pytest.mark.asyncio
async def test_has_default_login():
    """Default login check uses fetchval."""
    conn = _FakeConn(val=1)
    repo = ContactUnitsRepository(db_connection=conn)

    assert await repo.has_default_login(organization_id=ORG_ID, contact_id=CONTACT_ID) is True


@pytest.mark.asyncio
async def test_find_active_primary_conflicts_empty():
    """Empty ids returns empty list without query."""
    conn = _FakeConn()
    repo = ContactUnitsRepository(db_connection=conn)

    assert (
        await repo.find_active_primary_conflicts(
            organization_id=ORG_ID,
            contact_id=CONTACT_ID,
            contact_unit_ids=[],
        )
        == []
    )
    assert not conn.fetch_calls


@pytest.mark.asyncio
async def test_unit_has_primary_occupant_with_exclude():
    """Primary occupant check optionally excludes contact."""
    conn = _FakeConn(row={"?column?": 1})
    repo = ContactUnitsRepository(db_connection=conn)

    assert (
        await repo.unit_has_primary_occupant(
            organization_id=ORG_ID,
            unit_id=UNIT_ID,
            exclude_contact_id=CONTACT_ID,
        )
        is True
    )
    query, args = conn.fetchrow_calls[0]
    assert "contact_id <> $4::uuid" in query


@pytest.mark.asyncio
async def test_confirm_selection():
    """Confirm selection activates pending rows."""
    conn = _FakeConn(rows=[{"id": CU_ID, "status": ContactUnitStatus.ACTIVE.value}])
    repo = ContactUnitsRepository(db_connection=conn)

    rows = await repo.confirm_selection(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        contact_unit_ids=[CU_ID],
    )

    assert rows[0]["status"] == ContactUnitStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_activate_units_by_ids_empty():
    """Empty ids skips execute."""
    conn = _async_mock_conn()
    repo = ContactUnitsRepository(db_connection=conn)

    await repo.activate_units_by_ids(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        contact_unit_ids=[],
    )
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_set_default_login():
    """set_default_login clears others then sets target."""
    conn = _FakeConn(row={"id": CU_ID, "is_default_login": True})
    repo = ContactUnitsRepository(db_connection=conn)

    row = await repo.set_default_login(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        contact_unit_id=CU_ID,
    )

    assert row["is_default_login"] is True
    assert conn._txn_entered is True  # pylint: disable=protected-access
    assert len(conn.execute_calls) == 1
    assert len(conn.fetchrow_calls) == 1


@pytest.mark.asyncio
async def test_insert_allotment():
    """Insert allotment returns id and status."""
    conn = _FakeConn(row={"id": CU_ID, "status": ContactUnitStatus.PENDING.value})
    repo = ContactUnitsRepository(db_connection=conn)

    row = await repo.insert_allotment(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
        contact_id=CONTACT_ID,
    )

    assert row["id"] == CU_ID
    query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO contact_units" in query


@pytest.mark.asyncio
async def test_list_household_by_primary_with_unit_filter():
    """Household list optionally filters by unit_id."""
    conn = _FakeConn(rows=[{"contact_unit_id": CU_ID}])
    repo = ContactUnitsRepository(db_connection=conn)

    rows = await repo.list_household_by_primary(
        organization_id=ORG_ID,
        primary_contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
    )

    assert rows[0]["contact_unit_id"] == CU_ID
    query, args = conn.fetch_calls[0]
    assert "primary_cu.unit_id" in query
    assert UNIT_ID in args


@pytest.mark.asyncio
async def test_delete_link_success():
    """delete_link returns True when row removed."""
    conn = _async_mock_conn(execute_result="DELETE 1")
    repo = ContactUnitsRepository(db_connection=conn)

    assert await repo.delete_link(organization_id=ORG_ID, contact_unit_id=CU_ID) is True


@pytest.mark.asyncio
async def test_count_links_for_contact():
    """Link count coerces None to zero."""
    conn = _async_mock_conn(val=None)
    repo = ContactUnitsRepository(db_connection=conn)

    assert await repo.count_links_for_contact(organization_id=ORG_ID, contact_id=CONTACT_ID) == 0


@pytest.mark.asyncio
async def test_get_unit_project():
    """Unit project lookup scopes org."""
    conn = _FakeConn(row={"id": UNIT_ID, "project_id": PROJECT_ID})
    repo = ContactUnitsRepository(db_connection=conn)

    row = await repo.get_unit_project(organization_id=ORG_ID, unit_id=UNIT_ID)

    assert row["project_id"] == PROJECT_ID
