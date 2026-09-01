"""Unit tests for WalkInRepository with fake connection."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from apps.user_service.app.db.repositories.walk_in_repository import WalkInRepository

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
ENTRY_ID = "770e8400-e29b-41d4-a716-446655440002"
VISIT_UNIT_ID = "880e8400-e29b-41d4-a716-446655440003"
TOWER_ID = "990e8400-e29b-41d4-a716-446655440004"
UNIT_ID = "aa0e8400-e29b-41d4-a716-446655440005"
CONTACT_ID = "bb0e8400-e29b-41d4-a716-446655440006"


class _FakeConn:
    """Minimal fake asyncpg connection."""

    def __init__(self, *, rows=None, row=None, fetchval=None):
        self.rows = rows or []
        self.row = row
        self.fetchval_result = fetchval
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self._fetchrow_queue: list[dict | None] = []

    def queue_fetchrow(self, *rows: dict | None) -> None:
        """Return successive fetchrow results."""
        self._fetchrow_queue.extend(rows)

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        if self._fetchrow_queue:
            return self._fetchrow_queue.pop(0)
        return self.row

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.fetchval_result


@pytest.mark.asyncio
async def test_fetch_units_for_flats_empty_input():
    conn = _FakeConn()
    repo = WalkInRepository(db_connection=conn)

    assert (
        await repo.fetch_units_for_flats(
            organization_id=ORG_ID,
            project_id=PROJECT_ID,
            flats=[],
        )
        == []
    )
    assert conn.fetch_calls == []


@pytest.mark.asyncio
async def test_fetch_units_for_flats_validates_tower_match():
    conn = _FakeConn(
        rows=[
            {
                "unit_id": UNIT_ID,
                "tower_id": TOWER_ID,
                "unit_code": "A-101",
                "unit_label": "A-101",
                "project_id": PROJECT_ID,
            }
        ]
    )
    repo = WalkInRepository(db_connection=conn)

    validated = await repo.fetch_units_for_flats(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        flats=[{"unit_id": UNIT_ID, "tower_id": TOWER_ID}],
    )
    assert validated[0]["unit_code"] == "A-101"
    assert "units u" in conn.fetch_calls[0][0]


@pytest.mark.asyncio
async def test_fetch_units_for_flats_rejects_mismatched_tower():
    conn = _FakeConn(
        rows=[
            {
                "unit_id": UNIT_ID,
                "tower_id": "other-tower",
                "unit_code": "A-101",
            }
        ]
    )
    repo = WalkInRepository(db_connection=conn)

    result = await repo.fetch_units_for_flats(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        flats=[{"unit_id": UNIT_ID, "tower_id": TOWER_ID}],
    )
    assert result == []


@pytest.mark.asyncio
async def test_fetch_units_for_flats_rejects_missing_unit():
    conn = _FakeConn(rows=[])
    repo = WalkInRepository(db_connection=conn)

    result = await repo.fetch_units_for_flats(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        flats=[{"unit_id": UNIT_ID, "tower_id": TOWER_ID}],
    )
    assert result == []


@pytest.mark.asyncio
async def test_insert_entry():
    conn = _FakeConn(row={"id": ENTRY_ID, "visitor_first_name": "Jane"})
    repo = WalkInRepository(db_connection=conn)

    row = await repo.insert_entry(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        visitor_first_name="Jane",
        visitor_last_name="Doe",
        visitor_phone_isd_code="+91",
        visitor_phone_number="9876543210",
        visitor_photo_paths=["photo.jpg"],
        vehicle_photo_paths=[],
        notes="Delivery",
        flats_count=1,
        requested_by_user_id=CONTACT_ID,
        gate_id=None,
    )

    assert row["id"] == ENTRY_ID
    assert "INSERT INTO walk_in_entries" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_insert_visit_unit():
    conn = _FakeConn(row={"id": VISIT_UNIT_ID, "sort_order": 0})
    repo = WalkInRepository(db_connection=conn)

    row = await repo.insert_visit_unit(
        organization_id=ORG_ID,
        walk_in_entry_id=ENTRY_ID,
        tower_id=TOWER_ID,
        unit_id=UNIT_ID,
        sort_order=0,
    )

    assert row["id"] == VISIT_UNIT_ID
    assert "walk_in_visit_units" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_insert_event_serializes_payload():
    conn = _FakeConn(row={"id": "event-1", "event_type": "requested"})
    repo = WalkInRepository(db_connection=conn)

    row = await repo.insert_event(
        organization_id=ORG_ID,
        walk_in_entry_id=ENTRY_ID,
        event_type="requested",
        actor_type="staff",
        actor_user_id=CONTACT_ID,
        payload={"flats_count": 1},
    )

    assert row["event_type"] == "requested"
    query, args = conn.fetchrow_calls[0]
    assert "::jsonb" in query
    assert "actor_label" not in query
    assert args[-1] == '{"flats_count": 1}'


@pytest.mark.asyncio
async def test_fetch_staff_display_names():
    conn = _FakeConn(
        rows=[
            {
                "user_id": CONTACT_ID,
                "salutation": "Mr",
                "first_name": "Ajay",
                "last_name": "Guard",
                "email": "guard@example.com",
            }
        ]
    )
    repo = WalkInRepository(db_connection=conn)

    names = await repo.fetch_staff_display_names(
        organization_id=ORG_ID,
        user_ids=[CONTACT_ID],
    )

    assert names == {CONTACT_ID: "Mr Ajay Guard"}
    query, args = conn.fetch_calls[0]
    assert "FROM organization_members om" in query
    assert args == (ORG_ID, [CONTACT_ID])


@pytest.mark.asyncio
async def test_fetch_contact_display_names():
    conn = _FakeConn(
        rows=[
            {
                "contact_id": CONTACT_ID,
                "prefix": "Mr",
                "first_name": "Resident",
                "last_name": "Owner",
            }
        ]
    )
    repo = WalkInRepository(db_connection=conn)

    names = await repo.fetch_contact_display_names(
        organization_id=ORG_ID,
        contact_ids=[CONTACT_ID],
    )

    assert names == {CONTACT_ID: "Mr Resident Owner"}
    query, args = conn.fetch_calls[0]
    assert "FROM contacts ct" in query
    assert args == (ORG_ID, [CONTACT_ID])


@pytest.mark.asyncio
async def test_get_entry_with_and_without_project():
    conn = _FakeConn(row={"id": ENTRY_ID})
    repo = WalkInRepository(db_connection=conn)

    found = await repo.get_entry(
        organization_id=ORG_ID,
        walk_in_entry_id=ENTRY_ID,
        project_id=PROJECT_ID,
    )
    assert found["id"] == ENTRY_ID
    assert "project_id = $2::uuid" in conn.fetchrow_calls[0][0]

    conn.row = None
    missing = await repo.get_entry(
        organization_id=ORG_ID,
        walk_in_entry_id=ENTRY_ID,
    )
    assert missing is None
    assert "project_id" not in conn.fetchrow_calls[1][0]


@pytest.mark.asyncio
async def test_list_entries():
    conn = _FakeConn(rows=[{"id": ENTRY_ID, "primary_unit_label": "A-101"}])
    repo = WalkInRepository(db_connection=conn)

    rows = await repo.list_entries(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        status="awaiting",
        on_date=date(2026, 7, 28),
    )

    assert rows[0]["primary_unit_label"] == "A-101"
    query, args = conn.fetch_calls[0]
    assert "::walk_in_status" in query
    assert args[2] == "awaiting"
    assert args[3] == date(2026, 7, 28)


@pytest.mark.asyncio
async def test_list_visit_units_and_events():
    conn = _FakeConn(rows=[{"id": VISIT_UNIT_ID, "tower_name": "Tower A"}])
    repo = WalkInRepository(db_connection=conn)

    units = await repo.list_visit_units(
        organization_id=ORG_ID,
        walk_in_entry_id=ENTRY_ID,
    )
    assert units[0]["tower_name"] == "Tower A"

    conn.rows = [{"id": "event-1", "event_type": "requested"}]
    events = await repo.list_events(
        organization_id=ORG_ID,
        walk_in_entry_id=ENTRY_ID,
    )
    assert events[0]["event_type"] == "requested"
    assert "walk_in_events" in conn.fetch_calls[1][0]


@pytest.mark.asyncio
async def test_get_visit_unit_by_id_or_unit_id():
    conn = _FakeConn()
    conn.queue_fetchrow(None, {"id": VISIT_UNIT_ID, "unit_id": UNIT_ID})
    repo = WalkInRepository(db_connection=conn)

    found = await repo.get_visit_unit(
        organization_id=ORG_ID,
        walk_in_entry_id=ENTRY_ID,
        visit_unit_id=UNIT_ID,
    )

    assert found["unit_id"] == UNIT_ID
    assert len(conn.fetchrow_calls) == 2
    assert "vu.id = $3::uuid" in conn.fetchrow_calls[0][0]
    assert "vu.unit_id = $3::uuid" in conn.fetchrow_calls[1][0]


@pytest.mark.asyncio
async def test_get_visit_unit_not_found():
    conn = _FakeConn()
    conn.queue_fetchrow(None, None)
    repo = WalkInRepository(db_connection=conn)

    assert (
        await repo.get_visit_unit(
            organization_id=ORG_ID,
            walk_in_entry_id=ENTRY_ID,
            visit_unit_id=VISIT_UNIT_ID,
        )
        is None
    )


@pytest.mark.asyncio
async def test_update_visit_unit_status():
    conn = _FakeConn(row={"id": VISIT_UNIT_ID, "status": "approved"})
    repo = WalkInRepository(db_connection=conn)

    updated = await repo.update_visit_unit_status(
        organization_id=ORG_ID,
        walk_in_entry_id=ENTRY_ID,
        visit_unit_id=VISIT_UNIT_ID,
        status="approved",
        approved_by_contact_id=CONTACT_ID,
    )

    assert updated["status"] == "approved"
    query = conn.fetchrow_calls[0][0]
    assert "::walk_in_visit_unit_status" in query
    assert "approved_by_contact_id" in query

    conn.row = None
    assert (
        await repo.update_visit_unit_status(
            organization_id=ORG_ID,
            walk_in_entry_id=ENTRY_ID,
            visit_unit_id=VISIT_UNIT_ID,
            status="rejected",
            rejected_by_contact_id=CONTACT_ID,
            rejection_reason="Not expected",
        )
        is None
    )


@pytest.mark.asyncio
async def test_count_visit_units_by_status():
    conn = _FakeConn(row={"approved_count": 1, "awaiting_count": 0, "rejected_count": 0})
    repo = WalkInRepository(db_connection=conn)

    counts = await repo.count_visit_units_by_status(
        organization_id=ORG_ID,
        walk_in_entry_id=ENTRY_ID,
    )
    assert counts["approved_count"] == 1

    conn.row = None
    empty = await repo.count_visit_units_by_status(
        organization_id=ORG_ID,
        walk_in_entry_id=ENTRY_ID,
    )
    assert empty == {"approved_count": 0, "awaiting_count": 0, "rejected_count": 0}


@pytest.mark.asyncio
async def test_update_entry_header():
    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    conn = _FakeConn(row={"id": ENTRY_ID, "status": "entered"})
    repo = WalkInRepository(db_connection=conn)

    updated = await repo.update_entry_header(
        organization_id=ORG_ID,
        walk_in_entry_id=ENTRY_ID,
        status="entered",
        approved_flats_count=1,
        entered_at=now,
    )
    assert updated["status"] == "entered"
    assert "UPDATE walk_in_entries" in conn.fetchrow_calls[0][0]

    conn.row = None
    assert (
        await repo.update_entry_header(
            organization_id=ORG_ID,
            walk_in_entry_id=ENTRY_ID,
            status="exited",
            exited_at=now,
        )
        is None
    )


@pytest.mark.asyncio
async def test_reject_open_visit_units_for_unit():
    """Turnover reject query scopes to open visit units on awaiting/approved entries."""
    conn = _FakeConn(
        rows=[
            {
                "id": VISIT_UNIT_ID,
                "walk_in_entry_id": ENTRY_ID,
                "unit_id": UNIT_ID,
                "tower_id": TOWER_ID,
                "status": "rejected",
            }
        ]
    )
    repo = WalkInRepository(db_connection=conn)

    rows = await repo.reject_open_visit_units_for_unit(
        organization_id=ORG_ID,
        unit_id=UNIT_ID,
        rejection_reason="Unit vacated",
    )

    assert rows[0]["walk_in_entry_id"] == ENTRY_ID
    query, args = conn.fetch_calls[0]
    assert "walk_in_visit_units vu" in query
    assert "walk_in_entries e" in query
    assert args[0] == ORG_ID
    assert args[1] == UNIT_ID
    assert args[2] == "Unit vacated"


@pytest.mark.asyncio
async def test_resident_can_act_on_unit():
    conn = _FakeConn(fetchval=1)
    repo = WalkInRepository(db_connection=conn)

    assert await repo.resident_can_act_on_unit(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
    )
    query, _ = conn.fetchval_calls[0]
    assert "contact_units cu" in query
    assert "contact_type" not in query

    conn.fetchval_result = None
    assert not await repo.resident_can_act_on_unit(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        unit_id=UNIT_ID,
    )


@pytest.mark.asyncio
async def test_list_resident_visit_units():
    conn = _FakeConn(rows=[{"visit_unit_id": VISIT_UNIT_ID, "status": "awaiting"}])
    repo = WalkInRepository(db_connection=conn)

    rows = await repo.list_resident_visit_units(
        organization_id=ORG_ID,
        contact_id=CONTACT_ID,
        status="awaiting",
    )

    assert rows[0]["visit_unit_id"] == VISIT_UNIT_ID
    query, args = conn.fetch_calls[0]
    assert "::walk_in_visit_unit_status" in query
    assert args[1] == "awaiting"
    assert "contact_type" not in query
    assert args[2] == CONTACT_ID
