"""Unit tests for NoticesRepository query building."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.app.db.repositories.notices_repository import NoticesRepository
from apps.user_service.app.schemas.enums import NoticeListStatus

ORG = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"
NOTICE = "33333333-3333-3333-3333-333333333333"


class _FakeConn:
    """Minimal fake asyncpg connection for repository tests."""

    def __init__(
        self,
        *,
        rows=None,
        row=None,
        val=0,
        execute_result: str = "UPDATE 0",
    ):
        self.rows = rows or []
        self.row = row
        self.val = val
        self.execute_result = execute_result
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
        return self.execute_result

    async def executemany(self, query, args):
        self.executemany_calls.append((query.strip(), args))


def _notice_row(**overrides) -> dict:
    base = {
        "id": NOTICE,
        "organization_id": ORG,
        "project_id": PROJECT,
        "display_code": "NTC-1",
        "title": "Pool closure",
        "status": "live",
        "pinned": False,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_allocate_sequence_number():
    conn = _FakeConn(row={"next_sequence": 5})
    repo = NoticesRepository(db_connection=conn)
    assert await repo.allocate_sequence_number(organization_id=ORG, project_id=PROJECT) == 5


@pytest.mark.asyncio
async def test_insert_notice_fetches_detail():
    conn = _FakeConn(
        row={"id": NOTICE, **_notice_row()},
    )
    repo = NoticesRepository(db_connection=conn)
    result = await repo.insert_notice(
        organization_id=ORG,
        project_id=PROJECT,
        display_code="NTC-1",
        sequence_number=1,
        title="Title",
        description="Desc",
        category="maintenance",
        status="draft",
        scope_type="whole_society",
        publish_at=None,
        published_at=None,
        duplicate_of_id=None,
        created_by_user_id="user-1",
        updated_by_user_id="user-1",
    )
    assert result["id"] == NOTICE
    insert_query, _ = conn.fetchrow_calls[0]
    assert "INSERT INTO notices" in insert_query
    assert "::notice_category" in insert_query


@pytest.mark.asyncio
async def test_update_notice_fields_empty_and_missing():
    conn = _FakeConn(row=_notice_row())
    repo = NoticesRepository(db_connection=conn)
    updated = await repo.update_notice_fields(
        organization_id=ORG,
        project_id=PROJECT,
        notice_id=NOTICE,
        fields={},
    )
    assert updated["id"] == NOTICE

    conn.row = None
    missing = await repo.update_notice_fields(
        organization_id=ORG,
        project_id=PROJECT,
        notice_id=NOTICE,
        fields={"title": "New"},
    )
    assert missing is None


@pytest.mark.asyncio
async def test_update_notice_fields_casts_enums():
    conn = _FakeConn(row={"id": NOTICE})
    repo = NoticesRepository(db_connection=conn)
    await repo.update_notice_fields(
        organization_id=ORG,
        project_id=PROJECT,
        notice_id=NOTICE,
        fields={"status": "live", "category": "maintenance"},
    )
    update_query, _ = conn.fetchrow_calls[0]
    assert "status = $4::notice_status" in update_query
    assert "category = $5::notice_category" in update_query


@pytest.mark.asyncio
async def test_replace_recipients_towers_attachments():
    conn = _FakeConn()
    repo = NoticesRepository(db_connection=conn)

    await repo.replace_recipients(
        organization_id=ORG,
        notice_id=NOTICE,
        recipient_groups=["Owner", "Tenant"],
    )
    assert len(conn.executemany_calls) == 1

    await repo.replace_recipients(
        organization_id=ORG,
        notice_id=NOTICE,
        recipient_groups=[],
    )
    assert "DELETE FROM notice_recipients" in conn.execute_calls[0][0]

    await repo.replace_towers(
        organization_id=ORG,
        notice_id=NOTICE,
        tower_ids=["tower-1"],
    )
    assert "INSERT INTO notice_towers" in conn.executemany_calls[-1][0]

    await repo.replace_attachments(
        organization_id=ORG,
        notice_id=NOTICE,
        attachments=[
            {
                "file_path": "org/notices/a.jpg",
                "file_name": "a.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": 100,
                "sort_order": 0,
            }
        ],
    )
    assert "INSERT INTO notice_attachments" in conn.executemany_calls[-1][0]


@pytest.mark.asyncio
async def test_list_recipient_groups_and_towers():
    conn = _FakeConn(rows=[{"recipient_group": "Owner"}])
    repo = NoticesRepository(db_connection=conn)
    groups = await repo.list_recipient_groups(organization_id=ORG, notice_id=NOTICE)
    assert groups == ["Owner"]

    conn.rows = [{"tower_id": "tower-1", "name": "Tower A"}]
    towers = await repo.list_towers_for_notice(organization_id=ORG, notice_id=NOTICE)
    assert towers[0]["tower_id"] == "tower-1"


@pytest.mark.asyncio
async def test_get_summary_counts():
    conn = _FakeConn(
        row={"all_count": 10, "live_count": 5, "scheduled_count": 2, "deleted_count": 3},
        rows=[{"recipient_group": "Owner", "cnt": 4}],
    )
    repo = NoticesRepository(db_connection=conn)
    summary = await repo.get_summary_counts(organization_id=ORG, project_id=PROJECT)
    assert summary["all"] == 10
    assert summary["live_by_group"]["Owner"] == 4


@pytest.mark.asyncio
async def test_list_notices_status_filters():
    conn = _FakeConn(row={"total": 2}, rows=[_notice_row()])
    repo = NoticesRepository(db_connection=conn)

    for status, fragment in [
        (NoticeListStatus.ALL, "status <> 'deleted'"),
        (NoticeListStatus.LIVE, "status = 'live'"),
        (NoticeListStatus.SCHEDULED, "status = 'scheduled'"),
        (NoticeListStatus.DELETED, "status = 'deleted'"),
    ]:
        conn.fetchrow_calls.clear()
        conn.fetch_calls.clear()
        items, total = await repo.list_notices(
            organization_id=ORG,
            project_id=PROJECT,
            status=status,
            group=None,
            search=None,
            limit=20,
            offset=0,
        )
        assert total == 2
        assert len(items) == 1
        assert fragment in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_list_notices_group_and_search():
    conn = _FakeConn(row={"total": 1}, rows=[_notice_row()])
    repo = NoticesRepository(db_connection=conn)
    await repo.list_notices(
        organization_id=ORG,
        project_id=PROJECT,
        status=NoticeListStatus.LIVE,
        group="Owner",
        search="pool",
        limit=10,
        offset=5,
    )
    list_query, _ = conn.fetch_calls[0]
    assert "notice_recipients" in list_query
    assert "ILIKE" in list_query


@pytest.mark.asyncio
async def test_list_attachments_for_notices_empty_and_grouped():
    conn = _FakeConn()
    repo = NoticesRepository(db_connection=conn)
    assert await repo.list_attachments_for_notices(organization_id=ORG, notice_ids=[]) == {}

    conn.rows = [
        {
            "notice_id": NOTICE,
            "id": "att-1",
            "file_path": "path.jpg",
            "file_name": "path.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": 50,
            "sort_order": 0,
        }
    ]
    grouped = await repo.list_attachments_for_notices(
        organization_id=ORG,
        notice_ids=[NOTICE],
    )
    assert grouped[NOTICE][0]["id"] == "att-1"


@pytest.mark.asyncio
async def test_pin_operations():
    conn = _FakeConn(
        row={"id": "pin-1", "slot_index": 1, "pin_duration": "manual"},
        rows=[{"slot_index": 1, "notice_id": NOTICE, "display_code": "NTC-1"}],
    )
    repo = NoticesRepository(db_connection=conn)

    await repo.deactivate_pins_for_notice(organization_id=ORG, notice_id=NOTICE)
    assert "UPDATE notice_pins" in conn.execute_calls[0][0]

    conn.row = {"id": "pin-1", "slot_index": 2, "pin_duration": "hours_24"}
    pin = await repo.get_active_pin_for_notice(organization_id=ORG, notice_id=NOTICE)
    assert pin["slot_index"] == 2

    conn.row = {"id": "pin-1", "notice_id": NOTICE, "slot_index": 1}
    slot_pin = await repo.get_active_pin_for_slot(
        organization_id=ORG,
        project_id=PROJECT,
        slot_index=1,
    )
    assert slot_pin["notice_id"] == NOTICE

    conn.row = {"cnt": 3}
    assert await repo.count_active_pins(organization_id=ORG, project_id=PROJECT) == 3

    pins = await repo.list_active_pins_with_notices(
        organization_id=ORG,
        project_id=PROJECT,
    )
    assert pins[0]["notice_id"] == NOTICE

    await repo.deactivate_pin_on_slot(
        organization_id=ORG,
        project_id=PROJECT,
        slot_index=1,
    )
    conn.row = {"id": "pin-2", "slot_index": 2, "pin_duration": "hours_72"}
    inserted = await repo.insert_pin(
        organization_id=ORG,
        project_id=PROJECT,
        notice_id=NOTICE,
        slot_index=2,
        pin_duration="hours_72",
        expires_at=datetime.now(timezone.utc),
    )
    assert inserted["slot_index"] == 2


@pytest.mark.asyncio
async def test_find_first_free_slot():
    conn = _FakeConn(rows=[{"slot_index": 1}, {"slot_index": 3}])
    repo = NoticesRepository(db_connection=conn)
    assert await repo.find_first_free_slot(organization_id=ORG, project_id=PROJECT) == 2

    conn.rows = [{"slot_index": i} for i in range(1, 7)]
    assert await repo.find_first_free_slot(organization_id=ORG, project_id=PROJECT) is None


@pytest.mark.asyncio
async def test_publish_due_scheduled_notices_and_expire_pins():
    conn = _FakeConn(rows=[{"id": NOTICE}], execute_result="UPDATE 4")
    repo = NoticesRepository(db_connection=conn)

    ids = await repo.publish_due_scheduled_notices(
        organization_id=ORG,
        project_id=PROJECT,
    )
    assert ids == [NOTICE]
    query, _ = conn.fetch_calls[0]
    assert "status = 'live'" in query
    assert "organization_id = $1::uuid" in query

    assert await repo.expire_due_pins() == 4


@pytest.mark.asyncio
async def test_like_operations_contact_and_user():
    conn = _FakeConn(row={"id": "like-1"})
    repo = NoticesRepository(db_connection=conn)

    assert await repo.upsert_like(
        organization_id=ORG,
        notice_id=NOTICE,
        contact_id="contact-1",
    )
    assert await repo.upsert_like(
        organization_id=ORG,
        notice_id=NOTICE,
        user_id="user-1",
    )
    assert not await repo.upsert_like(
        organization_id=ORG,
        notice_id=NOTICE,
    )

    conn.row = {"id": "like-1"}
    assert await repo.delete_like(
        organization_id=ORG,
        notice_id=NOTICE,
        contact_id="contact-1",
    )
    assert await repo.delete_like(
        organization_id=ORG,
        notice_id=NOTICE,
        user_id="user-1",
    )
    assert not await repo.delete_like(
        organization_id=ORG,
        notice_id=NOTICE,
    )

    conn.row = {"?": 1}
    assert await repo.contact_has_liked(
        organization_id=ORG,
        notice_id=NOTICE,
        contact_id="contact-1",
    )
    assert await repo.contact_has_liked(
        organization_id=ORG,
        notice_id=NOTICE,
        user_id="user-1",
    )
    conn.row = None
    assert not await repo.contact_has_liked(
        organization_id=ORG,
        notice_id=NOTICE,
    )


@pytest.mark.asyncio
async def test_delete_all_likes_for_contact():
    """Bulk delete removes likes for a contact and reconciles counts."""
    conn = _FakeConn(rows=[{"removed_count": 2}, {"removed_count": 1}])
    repo = NoticesRepository(db_connection=conn)

    removed = await repo.delete_all_likes_for_contact(
        organization_id=ORG,
        contact_id="contact-1",
    )

    assert removed == 3
    query, args = conn.fetch_calls[0]
    assert "DELETE FROM notice_likes" in query
    assert "UPDATE notices n" in query
    assert args == (ORG, "contact-1")


@pytest.mark.asyncio
async def test_upsert_like_noop_when_already_liked():
    conn = _FakeConn(row=None)
    repo = NoticesRepository(db_connection=conn)
    assert not await repo.upsert_like(
        organization_id=ORG,
        notice_id=NOTICE,
        contact_id="contact-1",
    )


@pytest.mark.asyncio
async def test_list_live_notices_for_resident_empty_ids():
    conn = _FakeConn()
    repo = NoticesRepository(db_connection=conn)
    items, total = await repo.list_live_notices_for_resident(
        organization_id=ORG,
        project_id=PROJECT,
        notice_ids=[],
        limit=10,
        offset=0,
    )
    assert items == []
    assert total == 0


@pytest.mark.asyncio
async def test_list_live_notices_for_resident_with_filters():
    conn = _FakeConn(row={"total": 1}, rows=[_notice_row()])
    repo = NoticesRepository(db_connection=conn)
    items, total = await repo.list_live_notices_for_resident(
        organization_id=ORG,
        project_id=PROJECT,
        notice_ids=[NOTICE],
        search="pool",
        limit=5,
        offset=0,
    )
    assert total == 1
    assert "ILIKE" in conn.fetchrow_calls[0][0]


@pytest.mark.asyncio
async def test_list_live_notices_for_resident_category_filter():
    conn = _FakeConn(row={"total": 1}, rows=[_notice_row()])
    repo = NoticesRepository(db_connection=conn)
    items, total = await repo.list_live_notices_for_resident(
        organization_id=ORG,
        project_id=PROJECT,
        notice_ids=[NOTICE],
        category="maintenance",
        limit=5,
        offset=0,
    )
    assert total == 1
    count_query, count_args = conn.fetchrow_calls[0]
    assert "n.category = $4::notice_category" in count_query
    assert count_args == (ORG, PROJECT, [NOTICE], "maintenance")
    list_query, list_args = conn.fetch_calls[0]
    assert "n.category = $4::notice_category" in list_query
    assert list_args[:4] == (ORG, PROJECT, [NOTICE], "maintenance")


@pytest.mark.asyncio
async def test_soft_delete_and_fetch_by_id_only():
    conn = _FakeConn(row={"id": NOTICE, "status": "deleted"})
    repo = NoticesRepository(db_connection=conn)

    deleted = await repo.soft_delete_notice(
        organization_id=ORG,
        project_id=PROJECT,
        notice_id=NOTICE,
        reason="outdated",
        updated_by_user_id="user-1",
    )
    assert deleted["status"] == "deleted"

    conn.row = _notice_row()
    found = await repo.fetch_notice_by_id_only(
        organization_id=ORG,
        notice_id=NOTICE,
    )
    assert found["id"] == NOTICE


@pytest.mark.asyncio
async def test_list_notices_published_since():
    conn = _FakeConn(rows=[{"id": NOTICE, "title": "Hello", "organization_id": ORG}])
    repo = NoticesRepository(db_connection=conn)
    rows = await repo.list_notices_published_since(notice_ids=[NOTICE])
    assert rows[0]["title"] == "Hello"

    assert await repo.list_notices_published_since(notice_ids=[]) == []


@pytest.mark.asyncio
async def test_increment_view_count():
    conn = _FakeConn()
    repo = NoticesRepository(db_connection=conn)
    await repo.increment_view_count(organization_id=ORG, notice_id=NOTICE)
    assert "view_count = view_count + 1" in conn.execute_calls[0][0]
