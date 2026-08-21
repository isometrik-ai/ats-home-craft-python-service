"""Unit tests for DailyHelpRepository query building."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from apps.user_service.app.db.repositories.daily_help_repository import (
    DailyHelpRepository,
)

ORG = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"
PROFILE = "33333333-3333-3333-3333-333333333333"
UNIT = "44444444-4444-4444-4444-444444444444"


class _FakeConn:
    def __init__(self, *, rows=None, row=None, val=0):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.fetch_calls: list[tuple] = []
        self.fetchrow_calls: list[tuple] = []
        self.fetchval_calls: list[tuple] = []
        self.execute_calls: list[tuple] = []
        self.executemany_calls: list[tuple] = []

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
        return "UPDATE 0"

    async def executemany(self, query, args):
        self.executemany_calls.append((query.strip(), args))

    def transaction(self):
        return _FakeTransaction()


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _profile_row(**overrides) -> dict:
    base = {
        "id": PROFILE,
        "organization_id": ORG,
        "project_id": PROJECT,
        "display_name": "Mrs. Lakshmi",
        "status": "active",
        "gate_passcode": "4821",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_generate_unique_passcode_success_and_failure():
    conn = _FakeConn(val=None)
    repo = DailyHelpRepository(db_connection=conn)

    with patch.object(DailyHelpRepository, "_random_passcode", return_value="1234"):
        code = await repo.generate_unique_passcode(
            organization_id=ORG,
            project_id=PROJECT,
        )
    assert code == "1234"

    conn.val = 1
    with patch.object(DailyHelpRepository, "_random_passcode", return_value="1234"):
        with pytest.raises(RuntimeError, match="passcode generation exhausted"):
            await repo.generate_unique_passcode(
                organization_id=ORG,
                project_id=PROJECT,
            )


@pytest.mark.asyncio
async def test_link_pass_id_and_insert_profile():
    conn = _FakeConn(row={"id": PROFILE, "linked_pass_id": "pass-1", **_profile_row()})
    repo = DailyHelpRepository(db_connection=conn)

    linked = await repo.link_pass_id(
        organization_id=ORG,
        project_id=PROJECT,
        profile_id=PROFILE,
        pass_id="pass-1",
    )
    assert linked["linked_pass_id"] == "pass-1"

    conn.row = _profile_row()
    created = await repo.insert_profile(
        organization_id=ORG,
        project_id=PROJECT,
        initials="Mrs.",
        first_name="Lakshmi",
        middle_name=None,
        last_name="Devi",
        display_name="Mrs. Lakshmi Devi",
        phone_isd_code="+91",
        phone_number="9655011223",
        alternate_phone_isd_code=None,
        alternate_phone_number=None,
        category_id="cat-1",
        gender=None,
        date_of_birth=None,
        photo_path=None,
        gate_passcode="4821",
        status="active",
        open_to_work=False,
        created_by_user_id="user-1",
    )
    assert created["display_name"] == "Mrs. Lakshmi"


@pytest.mark.asyncio
async def test_update_and_get_profile():
    conn = _FakeConn(row=_profile_row())
    repo = DailyHelpRepository(db_connection=conn)

    updated = await repo.update_profile(
        organization_id=ORG,
        project_id=PROJECT,
        profile_id=PROFILE,
        fields={"display_name": "Updated Name"},
        updated_by_user_id="user-2",
    )
    assert updated["display_name"] == "Mrs. Lakshmi"

    found = await repo.get_profile(
        organization_id=ORG,
        project_id=PROJECT,
        profile_id=PROFILE,
    )
    assert found["id"] == PROFILE

    conn.row = None
    assert (
        await repo.get_profile(
            organization_id=ORG,
            project_id=PROJECT,
            profile_id=PROFILE,
        )
        is None
    )


@pytest.mark.asyncio
async def test_list_profiles_and_summary():
    conn = _FakeConn(rows=[_profile_row()], val=1)
    repo = DailyHelpRepository(db_connection=conn)

    items, total = await repo.list_profiles(
        organization_id=ORG,
        project_id=PROJECT,
        status=None,
        category_id=None,
        search=None,
        limit=20,
        offset=0,
    )
    assert total == 1
    assert items[0]["id"] == PROFILE

    conn.row = {
        "total": 3,
        "pending_approval": 1,
        "rejected": 0,
        "active": 2,
        "inactive": 0,
        "deleted": 0,
    }
    summary = await repo.get_summary(organization_id=ORG, project_id=PROJECT)
    assert summary["active"] == 2


@pytest.mark.asyncio
async def test_documents_and_events():
    conn = _FakeConn(row={"id": "doc-1"}, rows=[{"id": "doc-1"}])
    repo = DailyHelpRepository(db_connection=conn)

    doc = await repo.insert_document(
        organization_id=ORG,
        profile_id=PROFILE,
        document_type="id_proof",
        label="ID",
        file_path="org/docs/a.pdf",
        file_name="a.pdf",
        mime_type="application/pdf",
        file_size_bytes=100,
        uploaded_by_user_id="user-1",
    )
    assert doc["id"] == "doc-1"

    docs = await repo.list_documents(
        organization_id=ORG,
        profile_id=PROFILE,
    )
    assert docs[0]["id"] == "doc-1"

    conn.row = {"id": "doc-1"}
    deleted = await repo.delete_document(
        organization_id=ORG,
        profile_id=PROFILE,
        document_id="doc-1",
    )
    assert deleted["id"] == "doc-1"

    conn.row = {"id": "event-1"}
    event = await repo.insert_event(
        organization_id=ORG,
        profile_id=PROFILE,
        event_type="created",
        actor_user_id="user-1",
        payload={"source": "test"},
    )
    assert event["id"] == "event-1"

    conn.rows = [{"id": "event-1"}]
    events = await repo.list_events(
        organization_id=ORG,
        profile_id=PROFILE,
    )
    assert events[0]["id"] == "event-1"


@pytest.mark.asyncio
async def test_household_links():
    conn = _FakeConn(row={"id": "link-1"}, rows=[{"id": "link-1"}], val=1)
    repo = DailyHelpRepository(db_connection=conn)

    link = await repo.insert_link(
        organization_id=ORG,
        project_id=PROJECT,
        profile_id=PROFILE,
        unit_id=UNIT,
        linked_by_contact_id="contact-1",
    )
    assert link["id"] == "link-1"

    removed = await repo.remove_link(
        organization_id=ORG,
        profile_id=PROFILE,
        link_id="link-1",
        removal_reason="moved out",
    )
    assert removed["id"] == "link-1"

    links = await repo.list_active_links_for_profile(
        organization_id=ORG,
        profile_id=PROFILE,
    )
    assert links[0]["id"] == "link-1"

    unit_links = await repo.list_active_links_for_unit(
        organization_id=ORG,
        unit_id=UNIT,
    )
    assert unit_links[0]["id"] == "link-1"

    assert (
        await repo.count_active_links_for_unit(
            organization_id=ORG,
            unit_id=UNIT,
        )
        == 1
    )

    assert (
        await repo.list_links_for_units(
            organization_id=ORG,
            unit_ids=[],
        )
        == []
    )

    conn.rows = [{"id": "link-1"}]
    multi = await repo.list_links_for_units(
        organization_id=ORG,
        unit_ids=[UNIT],
        profile_id=PROFILE,
    )
    assert multi[0]["id"] == "link-1"

    assert await repo.has_active_link(
        organization_id=ORG,
        profile_id=PROFILE,
        unit_id=UNIT,
    )


@pytest.mark.asyncio
async def test_ratings():
    conn = _FakeConn(
        row={"id": "rating-1", "stars": Decimal("4.5"), "comment": "Great", "created_at": "now"},
        rows=[{"trait": "punctual"}],
    )
    repo = DailyHelpRepository(db_connection=conn)

    rating = await repo.insert_rating(
        organization_id=ORG,
        project_id=PROJECT,
        profile_id=PROFILE,
        unit_id=UNIT,
        rated_by_contact_id="contact-1",
        stars=Decimal("4.5"),
        comment="Great",
        traits=["punctual"],
    )
    assert rating["id"] == "rating-1"

    found = await repo.get_rating_by_rater(
        organization_id=ORG,
        profile_id=PROFILE,
        unit_id=UNIT,
        rated_by_contact_id="contact-1",
    )
    assert found["stars"] == Decimal("4.5")

    updated = await repo.update_rating(
        organization_id=ORG,
        profile_id=PROFILE,
        unit_id=UNIT,
        rated_by_contact_id="contact-1",
        stars=Decimal("5.0"),
        comment="Excellent",
        traits=["punctual"],
    )
    assert updated is not None

    conn.rows = [
        {
            "profile_id": PROFILE,
            "rating_count": 2,
            "average_stars": Decimal("4.50"),
        }
    ]
    batch = await repo.get_rating_summaries_batch(
        organization_id=ORG,
        profile_ids=[PROFILE],
    )
    assert batch[PROFILE]["rating_count"] == 2

    conn.rows = [{"trait": "punctual", "count": 1}]
    with patch.object(
        repo,
        "get_rating_summaries_batch",
        new=AsyncMock(
            return_value={
                PROFILE: {"rating_count": 2, "average_stars": 4.5},
            }
        ),
    ):
        summary = await repo.get_rating_summary(
            organization_id=ORG,
            profile_id=PROFILE,
        )
    assert summary["rating_count"] == 2
    assert summary["trait_counts"]["punctual"] == 1


@pytest.mark.asyncio
async def test_slots_and_attendance():
    conn = _FakeConn(rows=[{"period": "morning"}], row={"id": "absence-1"})
    repo = DailyHelpRepository(db_connection=conn)

    slots = await repo.list_slots(
        organization_id=ORG,
        profile_id=PROFILE,
    )
    assert slots[0]["period"] == "morning"

    cleared = await repo.replace_slots(
        organization_id=ORG,
        profile_id=PROFILE,
        slots=[],
    )
    assert cleared == []
    assert "DELETE FROM daily_help_availability_slots" in conn.execute_calls[0][0]

    with patch.object(
        DailyHelpRepository,
        "bulk_insert_returning",
        new=AsyncMock(return_value=[{"id": "slot-1"}]),
    ):
        inserted = await repo.replace_slots(
            organization_id=ORG,
            profile_id=PROFILE,
            slots=[{"period": "morning", "start_time": "09:00", "end_time": "17:00"}],
        )
    assert inserted[0]["id"] == "slot-1"

    conn.rows = [{"attendance_date": "2026-05-10"}]
    dates = await repo.list_attendance_absence_dates_for_month(
        organization_id=ORG,
        profile_id=PROFILE,
        unit_id=UNIT,
        year=2026,
        month=5,
    )
    assert dates[0] == "2026-05-10"

    conn.row = {"id": "absence-1"}
    absence = await repo.upsert_attendance_absence(
        organization_id=ORG,
        project_id=PROJECT,
        profile_id=PROFILE,
        unit_id=UNIT,
        marked_by_contact_id="contact-1",
        attendance_date="2026-05-10",
    )
    assert absence["id"] == "absence-1"
