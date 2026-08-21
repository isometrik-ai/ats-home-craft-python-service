"""Unit tests for CommunityEventsRepository query building."""

from __future__ import annotations

from datetime import date

import pytest

from apps.user_service.app.db.repositories.community_events_repository import (
    CommunityEventsRepository,
)
from apps.user_service.app.schemas.enums import CommunityEventListTab

ORG = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"
EVENT = "33333333-3333-3333-3333-333333333333"
BOOKING = "44444444-4444-4444-4444-444444444444"


class _FakeConn:
    """Minimal fake asyncpg connection for repository tests."""

    def __init__(self, *, rows=None, row=None, val=0, execute_result: str = "UPDATE 0"):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.execute_result = execute_result
        self.fetch_calls: list[tuple] = []
        self.fetchrow_calls: list[tuple] = []
        self.fetchval_calls: list[tuple] = []
        self.execute_calls: list[tuple] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query.strip(), args))
        return self.val

    async def execute(self, query, *args):
        self.execute_calls.append((query.strip(), args))
        return self.execute_result


def _event_row(**overrides) -> dict:
    base = {
        "id": EVENT,
        "organization_id": ORG,
        "project_id": PROJECT,
        "display_code": "EVT-1",
        "title": "Summer fest",
        "publish_status": "published",
        "record_status": "active",
        "start_date": date.today(),
        "end_date": date.today(),
    }
    base.update(overrides)
    return base


def _event_data(**overrides) -> dict:
    base = {
        "display_code": "EVT-1",
        "sequence_number": 1,
        "title": "Summer fest",
        "category": "community",
        "publish_status": "draft",
        "start_date": date.today(),
        "end_date": date.today(),
        "event_type": "free",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_allocate_sequences():
    conn = _FakeConn(row={"next_sequence": 3})
    repo = CommunityEventsRepository(db_connection=conn)
    assert await repo.allocate_event_sequence(organization_id=ORG, project_id=PROJECT) == 3
    assert await repo.allocate_booking_sequence(organization_id=ORG, project_id=PROJECT) == 3


@pytest.mark.asyncio
async def test_insert_and_fetch_event():
    conn = _FakeConn(row={"id": EVENT, **_event_row()})
    repo = CommunityEventsRepository(db_connection=conn)
    inserted = await repo.insert_event(
        organization_id=ORG,
        project_id=PROJECT,
        data=_event_data(),
    )
    assert inserted["id"] == EVENT
    insert_query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO community_events" in insert_query


@pytest.mark.asyncio
async def test_update_event_fields_empty_and_missing():
    conn = _FakeConn(row=_event_row())
    repo = CommunityEventsRepository(db_connection=conn)
    updated = await repo.update_event_fields(
        organization_id=ORG,
        project_id=PROJECT,
        event_id=EVENT,
        fields={},
    )
    assert updated["id"] == EVENT

    conn.row = None
    missing = await repo.update_event_fields(
        organization_id=ORG,
        project_id=PROJECT,
        event_id=EVENT,
        fields={"title": "Updated"},
    )
    assert missing is None


@pytest.mark.asyncio
async def test_fetch_event_variants():
    conn = _FakeConn(row=_event_row())
    repo = CommunityEventsRepository(db_connection=conn)

    found = await repo.fetch_event_by_id(
        organization_id=ORG,
        project_id=PROJECT,
        event_id=EVENT,
    )
    assert found["title"] == "Summer fest"

    resident = await repo.fetch_resident_event_by_id(
        organization_id=ORG,
        event_id=EVENT,
    )
    assert resident["publish_status"] == "published"
    assert "publish_status = 'published'" in conn.fetchrow_calls[-1][0]


@pytest.mark.asyncio
async def test_list_events_tabs_and_search():
    conn = _FakeConn(row={"total": 1}, rows=[_event_row()])
    repo = CommunityEventsRepository(db_connection=conn)

    for tab, fragment in [
        (CommunityEventListTab.ALL.value, "record_status = 'active'"),
        (CommunityEventListTab.DRAFT.value, "publish_status = $3"),
        (CommunityEventListTab.DELETED.value, "record_status = 'deleted'"),
    ]:
        conn.fetchrow_calls.clear()
        conn.fetch_calls.clear()
        items, total = await repo.list_events(
            organization_id=ORG,
            project_id=PROJECT,
            tab=tab,
            search="summer",
            limit=10,
            offset=0,
        )
        assert total == 1
        assert fragment in conn.fetchrow_calls[0][0]
        assert "ILIKE" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_get_summary_counts():
    conn = _FakeConn(row={"total_events": 5, "upcoming": 2, "tab_all": 5})
    repo = CommunityEventsRepository(db_connection=conn)
    summary = await repo.get_summary_counts(organization_id=ORG, project_id=PROJECT)
    assert summary["total_events"] == 5


@pytest.mark.asyncio
async def test_replace_and_list_gallery():
    conn = _FakeConn(rows=[{"id": "media-1", "file_path": "img.jpg", "sort_order": 0}])
    repo = CommunityEventsRepository(db_connection=conn)

    await repo.replace_gallery(
        organization_id=ORG,
        project_id=PROJECT,
        event_id=EVENT,
        items=[
            {
                "file_path": "img.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": 100,
            }
        ],
    )
    assert "DELETE FROM community_event_media" in conn.execute_calls[0][0]
    assert "INSERT INTO community_event_media" in conn.execute_calls[1][0]

    gallery = await repo.list_gallery(organization_id=ORG, event_id=EVENT)
    assert gallery[0]["id"] == "media-1"


@pytest.mark.asyncio
async def test_booking_crud_and_filters():
    conn = _FakeConn(row={"id": BOOKING, "total": 1})
    repo = CommunityEventsRepository(db_connection=conn)

    booking = await repo.insert_booking(
        organization_id=ORG,
        project_id=PROJECT,
        data={
            "event_id": EVENT,
            "display_code": "BKG-1",
            "sequence_number": 1,
            "contact_id": "contact-1",
            "unit_id": "unit-1",
            "adult_tickets": 2,
            "child_tickets": 0,
            "total_tickets": 2,
            "subtotal_minor": 0,
            "tax_minor": 0,
            "total_amount_minor": 0,
            "booking_status": "confirmed",
            "payment_status": "not_applicable",
        },
    )
    assert booking["id"] == BOOKING

    conn.row = {"id": BOOKING, "event_id": EVENT}
    fetched = await repo.fetch_booking_by_id(
        organization_id=ORG,
        booking_id=BOOKING,
        event_id=EVENT,
    )
    assert fetched["event_id"] == EVENT

    conn.row = {"id": BOOKING, "event_title": "Summer fest"}
    by_token = await repo.fetch_booking_by_gate_token(
        organization_id=ORG,
        gate_qr_token="qr-token",
    )
    assert by_token["event_title"] == "Summer fest"


@pytest.mark.asyncio
async def test_list_bookings_for_event_with_status_filters():
    conn = _FakeConn(row={"total": 1}, rows=[{"id": BOOKING}])
    repo = CommunityEventsRepository(db_connection=conn)
    items, total = await repo.list_bookings_for_event(
        organization_id=ORG,
        project_id=PROJECT,
        event_id=EVENT,
        booking_status="confirmed",
        payment_status="paid",
        limit=20,
        offset=0,
    )
    assert total == 1
    query, _ = conn.fetchrow_calls[0]
    assert "booking_status = $4" in query
    assert "payment_status = $5" in query


@pytest.mark.asyncio
async def test_count_active_tickets_and_update_booking():
    conn = _FakeConn(row={"total": 3})
    repo = CommunityEventsRepository(db_connection=conn)
    assert (
        await repo.count_active_tickets_for_contact(
            organization_id=ORG,
            event_id=EVENT,
            contact_id="contact-1",
        )
        == 3
    )

    conn.row = {"id": BOOKING}
    updated = await repo.update_booking_fields(
        organization_id=ORG,
        booking_id=BOOKING,
        fields={"booking_status": "cancelled"},
    )
    assert updated["id"] == BOOKING

    conn.row = None
    assert (
        await repo.update_booking_fields(
            organization_id=ORG,
            booking_id=BOOKING,
            fields={"payment_status": "paid"},
        )
        is None
    )


@pytest.mark.asyncio
async def test_adjust_aggregates_and_revenue():
    conn = _FakeConn()
    repo = CommunityEventsRepository(db_connection=conn)
    await repo.adjust_event_aggregates_on_booking(
        organization_id=ORG,
        event_id=EVENT,
        tickets_delta=3,
        bookings_delta=1,
    )
    assert "tickets_booked" in conn.execute_calls[0][0]

    await repo.increment_paid_revenue(
        organization_id=ORG,
        event_id=EVENT,
        amount_minor=5000,
    )
    assert "revenue_collected_minor" in conn.execute_calls[1][0]


@pytest.mark.asyncio
async def test_fetch_oldest_waitlisted_and_complete_past():
    conn = _FakeConn(row={"id": BOOKING}, rows=[{"id": EVENT}])
    repo = CommunityEventsRepository(db_connection=conn)

    waitlisted = await repo.fetch_oldest_waitlisted_booking(
        organization_id=ORG,
        event_id=EVENT,
    )
    assert waitlisted["id"] == BOOKING

    completed = await repo.complete_past_events()
    assert completed == [EVENT]


@pytest.mark.asyncio
async def test_resident_list_events():
    conn = _FakeConn(row={"total": 2}, rows=[_event_row()])
    repo = CommunityEventsRepository(db_connection=conn)
    events, total = await repo.list_resident_events(
        organization_id=ORG,
        project_id=PROJECT,
        timeframe="upcoming",
        category=None,
        search=None,
        contact_id="contact-1",
        limit=10,
        offset=0,
    )
    assert total == 2
    assert events[0]["id"] == EVENT


@pytest.mark.asyncio
async def test_resident_booking_helpers():
    conn = _FakeConn(
        row={"active_ticket_count": 4, "active_booking_count": 2},
        rows=[{"booking_id": BOOKING}],
    )
    repo = CommunityEventsRepository(db_connection=conn)

    summary = await repo.sum_my_active_tickets(
        organization_id=ORG,
        project_id=PROJECT,
        contact_id="contact-1",
        unit_id="unit-1",
    )
    assert summary["active_ticket_count"] == 4

    bookings = await repo.list_my_bookings(
        organization_id=ORG,
        project_id=PROJECT,
        contact_id="contact-1",
        unit_id="unit-1",
    )
    assert bookings[0]["booking_id"] == BOOKING

    conn.row = {"id": BOOKING}
    mine = await repo.get_my_booking_for_event(
        organization_id=ORG,
        event_id=EVENT,
        contact_id="contact-1",
        unit_id="unit-1",
    )
    assert mine["id"] == BOOKING

    conn.row = {"?": 1}
    assert await repo.contact_has_owner_or_tenant_on_unit(
        organization_id=ORG,
        contact_id="contact-1",
        unit_id="unit-1",
    )


@pytest.mark.asyncio
async def test_insert_audit_log_and_exports():
    conn = _FakeConn(rows=[_event_row()], row={"total": 1})
    repo = CommunityEventsRepository(db_connection=conn)

    await repo.insert_audit_log(
        organization_id=ORG,
        project_id=PROJECT,
        event_id=EVENT,
        booking_id=BOOKING,
        action="published",
        actor_user_id="user-1",
        payload={"source": "test"},
    )
    assert "INSERT INTO community_event_audit_log" in conn.execute_calls[0][0]

    events = await repo.list_events_for_export(
        organization_id=ORG,
        project_id=PROJECT,
        tab=CommunityEventListTab.ALL.value,
        search=None,
        limit=100,
    )
    assert events[0]["id"] == EVENT

    conn.rows = [{"id": BOOKING}]
    bookings = await repo.list_bookings_for_export(
        organization_id=ORG,
        project_id=PROJECT,
        event_id=EVENT,
        limit=100,
    )
    assert bookings[0]["id"] == BOOKING


@pytest.mark.asyncio
async def test_reminder_queries():
    conn = _FakeConn(rows=[{"id": EVENT, "organization_id": ORG, "title": "Summer fest"}])
    repo = CommunityEventsRepository(db_connection=conn)
    due = await repo.list_events_due_for_reminder(hours_ahead=24)
    assert due[0]["title"] == "Summer fest"

    conn.rows = [{"contact_id": "contact-1", "user_id": "user-1"}]
    bookers = await repo.list_confirmed_bookers_for_reminder(
        organization_id=ORG,
        event_id=EVENT,
    )
    assert bookers[0]["contact_id"] == "contact-1"
