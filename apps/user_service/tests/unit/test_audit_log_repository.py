"""Unit tests for AuditLogRepository with fake asyncpg connection."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.audit_log_repository import (
    AuditLogRepository,
)
from apps.user_service.app.schemas.audit_logs import AuditLogFilter


def _async_mock_conn(*, rows=None, row=None, val=None):
    """Build asyncpg-like connection mock using AsyncMock."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetchval = AsyncMock(return_value=val)
    conn.execute = AsyncMock(return_value="DELETE 0")
    return conn


def _sql_args(mock_method):
    """Return (query, param_tuple) from an AsyncMock DB call."""
    parts = mock_method.await_args.args
    return parts[0], parts[1:]


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, val=None):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.fetch_calls = []
        self.fetchrow_calls = []
        self.fetchval_calls = []
        self.execute_calls = []

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
        return "DELETE 0"


def _filter(**overrides):
    """Build AuditLogFilter with defaults."""
    base = {
        "organization_id": "org-1",
        "search": None,
        "action_type": None,
        "table_name": None,
        "user_id": None,
        "start_date": None,
        "end_date": None,
        "limit": 20,
        "offset": 0,
    }
    base.update(overrides)
    return AuditLogFilter(**base)


def test_build_filters_org_only():
    """Base filter scopes by organization_id."""
    repo = AuditLogRepository(db_connection=None)
    where, params = repo._build_audit_log_filters(_filter())  # pylint: disable=protected-access

    assert "al.organization_id = $1" in where
    assert params == ["org-1"]


def test_build_filters_with_search_and_dates():
    """Optional filters append predicates and params."""
    repo = AuditLogRepository(db_connection=None)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)
    where, params = repo._build_audit_log_filters(  # pylint: disable=protected-access
        _filter(
            user_id="u1",
            action_type="UPDATE",
            table_name="leads",
            start_date=start,
            end_date=end,
            search="alpha",
        )
    )

    assert "al.user_id = $2" in where
    assert "al.action_type = $3" in where
    assert "al.table_name = $4" in where
    assert "al.timestamp >=" in where
    assert "ILIKE" in where
    assert params[-1] == "%alpha%"


@pytest.mark.asyncio
async def test_get_audit_logs_list_query():
    """List query joins auth.users and paginates."""
    conn = _FakeConn(rows=[{"id": "a1"}])
    repo = AuditLogRepository(db_connection=conn)

    rows = await repo.get_audit_logs_list(_filter(limit=10, offset=5))

    assert len(rows) == 1
    query, args = conn.fetch_calls[0]
    assert "FROM audit_logs al" in query
    assert "LEFT JOIN auth.users au" in query
    assert "ORDER BY al.timestamp DESC" in query
    assert args[-2:] == (10, 5)


@pytest.mark.asyncio
async def test_get_audit_logs_count():
    """Count query uses same filters without pagination."""
    conn = _FakeConn(val=42)
    repo = AuditLogRepository(db_connection=conn)

    count = await repo.get_audit_logs_count(_filter(table_name="leads"))

    assert count == 42
    query, args = conn.fetchval_calls[0]
    assert "SELECT COUNT(*)" in query
    assert "al.table_name = $2" in query
    assert args[0] == "org-1"


@pytest.mark.asyncio
async def test_get_audit_log_by_id():
    """Detail lookup scopes by org and user."""
    conn = _FakeConn(row={"id": "a1"})
    repo = AuditLogRepository(db_connection=conn)

    row = await repo.get_audit_log_by_id("a1", "org-1", "u1")

    assert row["id"] == "a1"
    query, args = conn.fetchrow_calls[0]
    assert "hash_signature" in query
    assert args == ("a1", "org-1", "u1")


@pytest.mark.asyncio
async def test_create_audit_log_jsonb_cast():
    """Insert casts JSONB columns."""
    conn = _FakeConn(row={"id": "a1"})
    repo = AuditLogRepository(db_connection=conn)

    result = await repo.create_audit_log(
        {
            "organization_id": "org-1",
            "action_type": "UPDATE",
            "old_values": {"data": {"name": "Old"}},
            "new_values": {"data": {"name": "New"}},
        }
    )

    assert result["id"] == "a1"
    query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO audit_logs" in query
    assert "::jsonb" in query


@pytest.mark.asyncio
async def test_bulk_create_audit_logs_empty():
    """Bulk insert returns empty list for empty input."""
    conn = _FakeConn()
    repo = AuditLogRepository(db_connection=conn)

    assert await repo.bulk_create_audit_logs([]) == []
    assert not conn.fetch_calls


@pytest.mark.asyncio
async def test_get_last_audit_log_hash():
    """Hash lookup orders by timestamp then id."""
    conn = _FakeConn(val="hash-abc")
    repo = AuditLogRepository(db_connection=conn)

    result = await repo.get_last_audit_log_hash("org-1")

    assert result == "hash-abc"
    query, args = conn.fetchval_calls[0]
    assert "hash_signature" in query
    assert args == ("org-1",)


@pytest.mark.asyncio
async def test_get_activity_logs_for_record():
    """Activity feed runs count then page query."""
    conn = _FakeConn(row={"total": 3}, rows=[{"id": "a1"}])
    repo = AuditLogRepository(db_connection=conn)

    items, total = await repo.get_activity_logs_for_record_with_actor_names(
        organization_id="org-1",
        table_name="leads",
        record_id="lead-1",
        limit=10,
        offset=0,
    )

    assert total == 3
    assert len(items) == 1
    assert len(conn.fetchrow_calls) == 1
    assert len(conn.fetch_calls) == 1
    assert "lead_stages old_ls" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_delete_all_audit_logs():
    """Delete all returns pre-delete count."""
    conn = _async_mock_conn(val=15)
    repo = AuditLogRepository(db_connection=conn)

    deleted = await repo.delete_all_audit_logs()

    assert deleted == 15
    assert conn.fetchval.await_count == 1
    conn.execute.assert_awaited_once()
    delete_query = conn.execute.await_args.args[0]
    assert "DELETE FROM audit_logs" in delete_query


@pytest.mark.asyncio
async def test_get_activity_logs_empty_rows():
    """Activity feed returns empty items when page has no rows."""
    conn = _async_mock_conn(row={"total": 0}, rows=[])
    repo = AuditLogRepository(db_connection=conn)

    items, total = await repo.get_activity_logs_for_record_with_actor_names(
        organization_id="org-1",
        table_name="leads",
        record_id="lead-1",
        limit=10,
        offset=0,
    )

    assert items == []
    assert total == 0


@pytest.mark.asyncio
async def test_create_audit_log_empty_data():
    """Create with all-None values returns empty dict."""
    conn = _async_mock_conn()
    repo = AuditLogRepository(db_connection=conn)

    assert await repo.create_audit_log({}) == {}
    conn.fetchrow.assert_not_called()


def test_extract_columns_ordered():
    """_extract_columns preserves COLUMN_ORDER."""
    repo = AuditLogRepository(db_connection=None)
    columns = repo._extract_columns(  # pylint: disable=protected-access
        [
            {"user_id": "u1", "action_type": "UPDATE", "organization_id": "org-1"},
            {"table_name": "leads", "record_id": "r1"},
        ]
    )

    assert columns.index("organization_id") < columns.index("user_id")
    assert "table_name" in columns


def test_build_placeholder_jsonb():
    """JSONB columns get ::jsonb cast in placeholder."""
    repo = AuditLogRepository(db_connection=None)

    assert repo._build_placeholder("old_values", 2) == "$2::jsonb"  # pylint: disable=protected-access
    assert repo._build_placeholder("action_type", 3) == "$3"  # pylint: disable=protected-access


def test_build_row_values():
    """Row builder aligns placeholders and params."""
    repo = AuditLogRepository(db_connection=None)
    placeholders, params = repo._build_row_values(  # pylint: disable=protected-access
        {"organization_id": "org-1", "old_values": {"a": 1}},
        ["organization_id", "old_values"],
        start_param_index=1,
    )

    assert placeholders == ["$1", "$2::jsonb"]
    assert params == ["org-1", {"a": 1}]


def test_build_bulk_insert_query():
    """Bulk insert builds multi-row VALUES clause."""
    repo = AuditLogRepository(db_connection=None)
    query, params = repo._build_bulk_insert_query(  # pylint: disable=protected-access
        ["organization_id", "action_type"],
        [
            {"organization_id": "org-1", "action_type": "CREATE"},
            {"organization_id": "org-1", "action_type": "UPDATE"},
        ],
    )

    assert "INSERT INTO audit_logs" in query
    assert query.count("VALUES") == 1
    assert len(params) == 4


@pytest.mark.asyncio
async def test_bulk_create_audit_logs_success():
    """Bulk insert returns created rows."""
    conn = _async_mock_conn(rows=[{"id": "a1"}, {"id": "a2"}])
    repo = AuditLogRepository(db_connection=conn)

    created = await repo.bulk_create_audit_logs(
        [
            {"organization_id": "org-1", "action_type": "CREATE"},
            {"organization_id": "org-1", "action_type": "UPDATE", "old_values": {"x": 1}},
        ]
    )

    assert len(created) == 2
    query, _ = _sql_args(conn.fetch)
    assert "INSERT INTO audit_logs" in query
    assert "::jsonb" in query
