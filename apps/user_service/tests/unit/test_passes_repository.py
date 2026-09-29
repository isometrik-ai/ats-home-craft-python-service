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


@pytest.mark.asyncio
async def test_list_bucket_upcoming_and_expired():
    """Upcoming and expired buckets add distinct predicates."""
    conn = _FakeConn(rows=[], val=0)
    repo = PassesRepository(db_connection=conn)

    await repo.list_by_contact(
        organization_id="org-1",
        host_contact_id="contact-1",
        bucket=PassListBucket.UPCOMING.value,
        page=1,
        page_size=20,
    )
    assert "p.valid_from > now()" in conn.fetchval_calls[0][0]

    conn.fetchval_calls.clear()
    await repo.list_by_contact(
        organization_id="org-1",
        host_contact_id="contact-1",
        bucket=PassListBucket.EXPIRED.value,
        page=1,
        page_size=20,
    )
    assert "p.valid_until < now()" in conn.fetchval_calls[0][0]


@pytest.mark.asyncio
async def test_list_display_status_expired_and_active():
    """Expired and active display filters compose not-used guard."""
    conn = _FakeConn(rows=[], val=0)
    repo = PassesRepository(db_connection=conn)

    await repo.list_by_contact(
        organization_id="org-1",
        host_contact_id="contact-1",
        display_status=PassDisplayStatus.EXPIRED.value,
        page=1,
        page_size=20,
    )
    assert "p.valid_until < now()" in conn.fetchval_calls[0][0]

    conn.fetchval_calls.clear()
    await repo.list_by_contact(
        organization_id="org-1",
        host_contact_id="contact-1",
        display_status=PassDisplayStatus.ACTIVE.value,
        page=1,
        page_size=20,
    )
    assert "p.valid_from <= now()" in conn.fetchval_calls[0][0]


@pytest.mark.asyncio
async def test_list_by_contact_unit_and_pass_type_filters():
    """Optional unit_id and pass_type filters append predicates."""
    conn = _FakeConn(rows=[], val=0)
    repo = PassesRepository(db_connection=conn)

    await repo.list_by_contact(
        organization_id="org-1",
        host_contact_id="contact-1",
        unit_id="unit-1",
        pass_type="guest",
        page=1,
        page_size=20,
    )
    count_query, _ = conn.fetchval_calls[0]
    assert "p.unit_id = $3::uuid" in count_query
    assert "p.pass_type = $4::pass_type" in count_query


@pytest.mark.asyncio
async def test_list_by_unit_scopes_project_and_unit():
    """Admin unit pass list filters by project_id and unit_id."""
    conn = _FakeConn(rows=[], val=0)
    repo = PassesRepository(db_connection=conn)

    await repo.list_by_unit(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        page=1,
        page_size=20,
    )
    count_query, _ = conn.fetchval_calls[0]
    assert "p.project_id = $2::uuid" in count_query
    assert "p.unit_id = $3::uuid" in count_query
    list_query, _ = conn.fetch_calls[0]
    assert "creator.first_name" in list_query


@pytest.mark.asyncio
async def test_get_owned_by_contact_and_get_by_id():
    """Owned and gate lookups return joined rows."""
    conn = _FakeConn(row={"id": "pass-1", "code": "4821"})
    repo = PassesRepository(db_connection=conn)

    owned = await repo.get_owned_by_contact(
        organization_id="org-1",
        host_contact_id="contact-1",
        pass_id="pass-1",
    )
    assert owned["code"] == "4821"

    by_id = await repo.get_by_id(organization_id="org-1", pass_id="pass-1")
    assert by_id["code"] == "4821"
    assert "host.first_name" in conn.fetchrow_calls[1][0]


@pytest.mark.asyncio
async def test_update_and_cancel_pass():
    """Update re-fetches owned pass; cancel scopes to active status."""
    conn = _FakeConn(
        row={"id": "pass-1", "code": "4821", "guest_name": "Guest"},
    )
    repo = PassesRepository(db_connection=conn)

    updated = await repo.update(
        organization_id="org-1",
        host_contact_id="contact-1",
        pass_id="pass-1",
        update_data={"guest_name": "Guest", "status": PassStatus.ACTIVE.value},
    )
    assert updated["guest_name"] == "Guest"
    assert "UPDATE passes p" in conn.fetchrow_calls[0][0]

    conn.row = {"id": "pass-1", "status": PassStatus.CANCELLED.value}
    cancelled = await repo.cancel(
        organization_id="org-1",
        host_contact_id="contact-1",
        pass_id="pass-1",
    )
    assert cancelled["status"] == PassStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_bucket_predicate_unknown_returns_empty():
    """Unknown bucket yields no extra SQL fragment."""
    sql, args = PassesRepository._bucket_predicate("unknown", param_index=3)
    assert sql == ""
    assert args == []


@pytest.mark.asyncio
async def test_list_visible_to_contact_includes_shared_non_private_predicate():
    """Visible list includes own passes and non-private passes on linked units."""
    conn = _FakeConn(rows=[], val=0)
    repo = PassesRepository(db_connection=conn)

    await repo.list_visible_to_contact(
        organization_id="org-1",
        viewer_contact_id="family-1",
        page=1,
        page_size=20,
    )
    count_query, _ = conn.fetchval_calls[0]
    assert "p.host_contact_id = $2::uuid" in count_query
    assert "COALESCE(p.is_private, false) = false" in count_query
    assert "FROM contact_units cu" in count_query
    assert "cu.contact_id = $2::uuid" in count_query


@pytest.mark.asyncio
async def test_get_visible_to_contact_uses_visibility_predicate():
    """Visible get scopes by pass id and viewer visibility rules."""
    conn = _FakeConn(row={"id": "pass-1", "code": "4821"})
    repo = PassesRepository(db_connection=conn)

    row = await repo.get_visible_to_contact(
        organization_id="org-1",
        viewer_contact_id="family-1",
        pass_id="pass-1",
    )
    assert row["code"] == "4821"
    query, _ = conn.fetchrow_calls[0]
    assert "p.id = $3::uuid" in query
    assert "COALESCE(p.is_private, false) = false" in query
    assert "FROM contact_units cu" in query


@pytest.mark.asyncio
async def test_display_status_predicate_unknown_returns_empty():
    """Unknown display_status adds no SQL fragment."""
    sql, args = PassesRepository._display_status_predicate("unknown", param_index=3)
    assert sql == ""
    assert args == []


@pytest.mark.asyncio
async def test_insert_daily_help_pass():
    """Daily help insert uses NULL unit/host columns."""
    conn = _FakeConn(row={"id": "pass-dh-1"})
    repo = PassesRepository(db_connection=conn)
    await repo.insert_daily_help(
        {
            "organization_id": "org-1",
            "project_id": "project-1",
            "daily_help_id": "dh-1",
            "pass_type": "daily_help",
            "guest_name": "Helper",
            "valid_from": "2026-07-10T09:00:00Z",
            "valid_until": "2026-12-31T21:00:00Z",
            "validity_type": "recurring",
            "code": "4821",
            "created_by_user_id": "user-1",
        }
    )
    query, _ = conn.fetchrow_calls[0]
    assert "daily_help_id" in query
    assert "NULL, NULL" in query.replace("\n", " ")


@pytest.mark.asyncio
async def test_update_daily_help_guest_snapshot_and_cancel_by_id():
    """Daily help snapshot update and staff cancel paths."""
    conn = _FakeConn(row={"id": "pass-1", "status": PassStatus.CANCELLED.value})
    repo = PassesRepository(db_connection=conn)

    updated = await repo.update_daily_help_guest_snapshot(
        organization_id="org-1",
        pass_id="pass-1",
        guest_name="Renamed",
        guest_phone_isd_code="+91",
        guest_phone_number="9999999999",
        pass_image_path=None,
    )
    assert updated["id"] == "pass-1"
    assert "daily_help_id IS NOT NULL" in conn.fetchrow_calls[0][0]

    cancelled = await repo.cancel_by_pass_id(organization_id="org-1", pass_id="pass-1")
    assert cancelled["status"] == PassStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_update_active_pass_code():
    """Active pass code rotation updates active rows only."""
    conn = _FakeConn(row={"id": "pass-1"})
    repo = PassesRepository(db_connection=conn)
    row = await repo.update_active_pass_code(
        organization_id="org-1",
        pass_id="pass-1",
        code="9999",
    )
    assert row["id"] == "pass-1"
    assert "SET code = $3" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_list_visible_to_contact_bucket_and_filters():
    """Visible list composes bucket, display, unit and pass_type filters."""
    conn = _FakeConn(rows=[], val=0)
    repo = PassesRepository(db_connection=conn)
    await repo.list_visible_to_contact(
        organization_id="org-1",
        viewer_contact_id="contact-1",
        bucket=PassListBucket.ACTIVE.value,
        display_status=PassDisplayStatus.ACTIVE.value,
        unit_id="unit-1",
        pass_type="guest",
        page=1,
        page_size=10,
    )
    count_query, count_args = conn.fetchval_calls[0]
    assert "p.valid_from <= now()" in count_query
    assert "p.unit_id" in count_query
    assert "p.pass_type" in count_query
    assert "unit-1" in count_args


@pytest.mark.asyncio
async def test_list_by_unit_optional_filters():
    """Unit admin list adds bucket, display and pass_type filters."""
    conn = _FakeConn(rows=[], val=0)
    repo = PassesRepository(db_connection=conn)
    await repo.list_by_unit(
        organization_id="org-1",
        project_id="project-1",
        unit_id="unit-1",
        bucket=PassListBucket.EXPIRED.value,
        display_status=PassDisplayStatus.USED.value,
        pass_type="guest",
        page=1,
        page_size=10,
    )
    count_query, _ = conn.fetchval_calls[0]
    assert "p.valid_until < now()" in count_query
    assert "p.pass_type" in count_query


@pytest.mark.asyncio
async def test_update_empty_data_refetches_owned_pass():
    """Empty update_data re-fetches owned pass without UPDATE."""
    conn = _FakeConn(row={"id": "pass-1", "code": "4821"})
    repo = PassesRepository(db_connection=conn)
    row = await repo.update(
        organization_id="org-1",
        host_contact_id="contact-1",
        pass_id="pass-1",
        update_data={},
    )
    assert row["code"] == "4821"
    assert len(conn.fetchrow_calls) == 1


@pytest.mark.asyncio
async def test_list_active_ids_and_active_for_unit():
    """Active pass id helpers scope by host and unit."""
    conn = _FakeConn(
        rows=[
            {"id": "pass-1", "host_contact_id": "contact-1"},
            {"id": "pass-2", "host_contact_id": "contact-2"},
        ]
    )
    repo = PassesRepository(db_connection=conn)
    ids = await repo.list_active_ids_for_host(
        organization_id="org-1",
        host_contact_id="contact-1",
    )
    assert ids == ["pass-1", "pass-2"]
    active = await repo.list_active_for_unit(organization_id="org-1", unit_id="unit-1")
    assert len(active) == 2


@pytest.mark.asyncio
async def test_update_returns_none_when_row_missing():
    """Update returns None when pass row is not owned by host."""
    conn = _FakeConn(row=None)
    repo = PassesRepository(db_connection=conn)
    result = await repo.update(
        organization_id="org-1",
        host_contact_id="contact-1",
        pass_id="pass-1",
        update_data={"guest_name": "Guest"},
    )
    assert result is None
