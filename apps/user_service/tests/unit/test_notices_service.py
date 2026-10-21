"""Unit tests for NoticesService validation helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.enums import (
    NOTICE_MAX_CONTACT_RECIPIENTS,
    NoticeCategory,
    NoticePinDuration,
    NoticePublishMode,
    NoticeRecipientGroup,
    NoticeRecipientInputGroup,
    NoticeScopeType,
    NoticeStatus,
)
from apps.user_service.app.schemas.notices import (
    CreateNoticeRequest,
    NoticeListQuery,
    UpdateNoticeRequest,
)
from apps.user_service.app.services.notices_service import NoticesService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
NOTICE_ID = "44444444-4444-4444-4444-444444444444"


def _service() -> NoticesService:
    svc = NoticesService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="user-1",
            email="owner@example.com",
            organization_id="org-1",
        ),
    )
    svc.repo = MagicMock()
    svc.repo.list_notices = AsyncMock(return_value=([], 0))
    svc.repo.list_attachments_for_notices = AsyncMock(return_value={})
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_contact_recipient_ids = AsyncMock(return_value=[])
    svc.repo.replace_contact_recipients = AsyncMock()
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.recipient_service.filter_valid_project_contact_ids = AsyncMock(return_value=[])
    return svc


def test_validate_content_title_too_long():
    with pytest.raises(ValidationException):
        NoticesService._validate_content(None, "x" * 71, "ok")


def test_validate_content_description_too_long():
    with pytest.raises(ValidationException):
        NoticesService._validate_content(None, "title", "x" * 601)


def test_validate_publish_requirements_missing_recipients():
    with pytest.raises(ValidationException):
        NoticesService._validate_publish_requirements(
            None,
            recipient_groups=[],
            contact_ids=[],
            scope_type=NoticeScopeType.WHOLE_SOCIETY,
            tower_ids=[],
        )


def test_validate_publish_requirements_allows_contact_ids_only():
    NoticesService._validate_publish_requirements(
        None,
        recipient_groups=[],
        contact_ids=["contact-1"],
        scope_type=NoticeScopeType.WHOLE_SOCIETY,
        tower_ids=[],
    )


def test_validate_publish_requirements_missing_towers():
    with pytest.raises(ValidationException):
        NoticesService._validate_publish_requirements(
            None,
            recipient_groups=["Owner"],
            contact_ids=[],
            scope_type=NoticeScopeType.BY_TOWER,
            tower_ids=[],
        )


def test_validate_schedule_too_far():
    future = datetime.now(timezone.utc) + timedelta(days=90)
    with pytest.raises(ValidationException):
        NoticesService._validate_schedule(None, future)


def test_assert_editable_live():
    with pytest.raises(ConflictException):
        NoticesService._assert_editable(None, NoticeStatus.LIVE.value)


def test_assert_duplicatable_live():
    with pytest.raises(ConflictException):
        NoticesService._assert_duplicatable(None, NoticeStatus.LIVE.value)


def test_resolve_publish_state_draft():
    status, publish_at, published_at = NoticesService._resolve_publish_state(
        None,
        publish_mode=NoticePublishMode.DRAFT,
        publish_at=None,
        recipient_groups=[],
        contact_ids=[],
        scope_type=NoticeScopeType.WHOLE_SOCIETY,
        tower_ids=[],
    )
    assert status == NoticeStatus.DRAFT.value
    assert publish_at is None
    assert published_at is None


def test_pin_expires_at_manual():
    assert NoticesService._pin_expires_at(NoticePinDuration.MANUAL) is None


@pytest.mark.asyncio
async def test_list_notices_includes_attachments():
    svc = _service()
    created_at = datetime.now(timezone.utc)
    svc.repo.list_notices = AsyncMock(
        return_value=(
            [
                {
                    "id": NOTICE_ID,
                    "display_code": "NTC-1",
                    "status": "live",
                    "title": "Pool closure",
                    "description": "Closed tomorrow",
                    "category": "maintenance",
                    "scope_type": "whole_society",
                    "recipient_groups": ["Owner"],
                    "scope_label": None,
                    "published_at": created_at,
                    "publish_at": None,
                    "deleted_at": None,
                    "pinned": False,
                    "pin_slot_index": None,
                    "view_count": 0,
                    "like_count": 0,
                    "created_at": created_at,
                }
            ],
            1,
        )
    )
    svc.repo.list_attachments_for_notices = AsyncMock(
        return_value={
            NOTICE_ID: [
                {
                    "id": "att-1",
                    "file_path": "org/project/notices/pool.jpg",
                    "file_name": "pool.jpg",
                    "mime_type": "image/jpeg",
                    "size_bytes": 1024,
                    "sort_order": 0,
                }
            ]
        }
    )

    items, total = await svc.list_notices(
        project_id=PROJECT_ID,
        query=NoticeListQuery(),
    )

    assert total == 1
    assert len(items[0].attachments) == 1
    assert items[0].attachments[0].file_path == "org/project/notices/pool.jpg"


def test_notice_publish_push_fields_include_deep_link_payload() -> None:
    """Published notice push includes data, entity, and click options."""
    fields = NoticesService._notice_publish_push_fields(
        project_id=PROJECT_ID,
        notice_id=NOTICE_ID,
        recipient_user_id="user-9",
    )

    assert fields["data"] == {
        "notice_id": NOTICE_ID,
        "project_id": PROJECT_ID,
        "screen": "notice_detail",
    }
    assert fields["entity"] == {"kind": "notice", "id": NOTICE_ID}
    assert fields["options"]["click_action"] == "OPEN_NOTICE"
    assert fields["options"]["idempotency_key"] == f"notice:{NOTICE_ID}:published:user-9"


def test_resolve_publish_state_now_and_scheduled():
    svc = _service()
    status, publish_at, published_at = svc._resolve_publish_state(
        publish_mode=NoticePublishMode.NOW,
        publish_at=None,
        recipient_groups=["Owner"],
        contact_ids=[],
        scope_type=NoticeScopeType.WHOLE_SOCIETY,
        tower_ids=[],
    )
    assert status == NoticeStatus.LIVE.value
    assert published_at is not None

    future = datetime.now(timezone.utc) + timedelta(days=1)
    status, publish_at, published_at = svc._resolve_publish_state(
        publish_mode=NoticePublishMode.SCHEDULE,
        publish_at=future,
        recipient_groups=["Owner"],
        contact_ids=[],
        scope_type=NoticeScopeType.WHOLE_SOCIETY,
        tower_ids=[],
    )
    assert status == NoticeStatus.SCHEDULED.value
    assert publish_at == future


def test_resolve_publish_state_schedule_required():
    svc = _service()
    with pytest.raises(ValidationException):
        svc._resolve_publish_state(
            publish_mode=NoticePublishMode.SCHEDULE,
            publish_at=None,
            recipient_groups=["Owner"],
            contact_ids=[],
            scope_type=NoticeScopeType.WHOLE_SOCIETY,
            tower_ids=[],
        )


def test_assert_editable_and_duplicatable_deleted():
    with pytest.raises(ConflictException):
        NoticesService._assert_editable(None, NoticeStatus.DELETED.value)
    with pytest.raises(ConflictException):
        NoticesService._assert_duplicatable(None, NoticeStatus.DELETED.value)


def test_pin_expires_at_timed_durations():
    assert NoticesService._pin_expires_at(NoticePinDuration.HOURS_24) is not None
    assert NoticesService._pin_expires_at(NoticePinDuration.HOURS_72) is not None


def test_validate_attachments_invalid_path():
    svc = _service()
    with pytest.raises(ValidationException):
        svc._validate_attachments(
            [
                type(
                    "A",
                    (),
                    {
                        "file_path": "org/other/file.jpg",
                        "mime_type": "image/jpeg",
                    },
                )()
            ],
        )


def test_is_valid_notice_attachment_path():
    assert NoticesService._is_valid_notice_attachment_path("org/project/notices/a.jpg")
    assert not NoticesService._is_valid_notice_attachment_path("org/other/a.jpg")


def test_groups_to_values():
    assert NoticesService._groups_to_values(None) == []
    assert NoticesService._groups_to_values(["Owner"]) == ["Owner"]
    assert NoticesService._groups_to_values(["All"]) == ["Owner", "Tenant", "Staff", "Security"]
    assert NoticesService._groups_to_values([NoticeRecipientInputGroup.ALL]) == [
        "Owner",
        "Tenant",
        "Staff",
        "Security",
    ]


def test_scope_label():
    svc = _service()
    assert svc._scope_label(NoticeScopeType.WHOLE_SOCIETY, []) == "Whole society"
    assert (
        svc._scope_label(
            NoticeScopeType.BY_TOWER,
            [{"tower_name": "Tower A"}, {"tower_name": "Tower B"}],
        )
        == "Tower A, Tower B"
    )


@pytest.mark.asyncio
async def test_get_summary():
    svc = _service()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo.get_summary_counts = AsyncMock(
        return_value={
            "all": 10,
            "live": 5,
            "scheduled": 2,
            "deleted": 3,
            "live_by_group": {"Owner": 4},
        }
    )
    summary = await svc.get_summary(project_id=PROJECT_ID)
    assert summary.all == 10
    assert summary.live_by_group["Owner"] == 4


@pytest.mark.asyncio
async def test_publish_due_notices_and_expire_pins():
    svc = _service()
    svc.repo.publish_due_scheduled_notices = AsyncMock(return_value=[NOTICE_ID])
    svc.repo.fetch_notice_by_id_only = AsyncMock(
        return_value={
            "id": NOTICE_ID,
            "project_id": PROJECT_ID,
            "title": "Hello",
            "scope_type": "whole_society",
        }
    )
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.fetch_notice_by_id = AsyncMock(return_value={"scope_type": "whole_society"})
    svc.recipient_service = MagicMock()
    svc.recipient_service.resolve_recipient_user_ids = AsyncMock(return_value=["user-1"])
    svc.push_dispatcher = MagicMock()
    svc.push_dispatcher.send_to_user = AsyncMock()

    ids = await svc.publish_due_notices(project_id=PROJECT_ID)
    assert ids == [NOTICE_ID]
    svc.push_dispatcher.send_to_user.assert_awaited_once()

    svc.repo.expire_due_pins = AsyncMock(return_value=2)
    assert await svc.expire_due_pins() == 2


@pytest.mark.asyncio
async def test_dispatch_publish_push_skips_missing_notice():
    svc = _service()
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=None)
    svc.push_dispatcher = MagicMock()
    svc.push_dispatcher.send_to_user = AsyncMock()

    await svc._dispatch_publish_push(
        project_id=PROJECT_ID,
        notice_id=NOTICE_ID,
        title="Hello",
    )
    svc.push_dispatcher.send_to_user.assert_not_awaited()


def _notice_detail_row(**overrides) -> dict:
    created_at = datetime.now(timezone.utc)
    base = {
        "id": NOTICE_ID,
        "organization_id": "org-1",
        "project_id": PROJECT_ID,
        "display_code": "NTC-1",
        "status": NoticeStatus.DRAFT.value,
        "title": "Pool closure",
        "description": "Closed tomorrow",
        "category": "maintenance",
        "scope_type": "whole_society",
        "recipient_groups": ["Owner"],
        "publish_at": None,
        "published_at": None,
        "deleted_at": None,
        "pinned": False,
        "pin_slot_index": None,
        "view_count": 0,
        "like_count": 0,
        "created_at": created_at,
        "updated_at": created_at,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_notice_draft():
    svc = _service()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo.allocate_sequence_number = AsyncMock(return_value=1)
    svc.repo.insert_notice = AsyncMock(return_value=_notice_detail_row())
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()
    svc.repo.replace_attachments = AsyncMock()
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=_notice_detail_row())

    body = CreateNoticeRequest(
        title="Pool closure",
        description="Closed tomorrow",
        category=NoticeCategory.MAINTENANCE,
        publish_mode=NoticePublishMode.DRAFT,
    )
    detail = await svc.create_notice(project_id=PROJECT_ID, body=body)
    assert detail.title == "Pool closure"
    svc.repo.insert_notice.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_notice_publish_now_dispatches_push():
    svc = _service()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo.allocate_sequence_number = AsyncMock(return_value=2)
    svc.repo.insert_notice = AsyncMock(
        return_value=_notice_detail_row(status=NoticeStatus.LIVE.value)
    )
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()
    svc.repo.replace_attachments = AsyncMock()
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.fetch_notice_by_id = AsyncMock(
        return_value=_notice_detail_row(status=NoticeStatus.LIVE.value)
    )
    svc.recipient_service = MagicMock()
    svc.recipient_service.resolve_recipient_user_ids = AsyncMock(return_value=[])
    svc.push_dispatcher = MagicMock()
    svc.push_dispatcher.send_to_user = AsyncMock()

    body = CreateNoticeRequest(
        title="Pool closure",
        description="Closed tomorrow",
        recipient_groups=[NoticeRecipientGroup.OWNER],
        publish_mode=NoticePublishMode.NOW,
    )
    await svc.create_notice(project_id=PROJECT_ID, body=body)
    svc.repo.insert_notice.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_notice_not_found():
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.update_notice(
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            body=UpdateNoticeRequest(title="Updated"),
        )


@pytest.mark.asyncio
async def test_delete_notice_not_found():
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=None)
    from libs.shared_utils.http_exceptions import NotFoundException

    with pytest.raises(NotFoundException):
        await svc.delete_notice(
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
        )


@pytest.mark.asyncio
async def test_delete_notice_soft_deletes_live():
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(
        return_value=_notice_detail_row(status=NoticeStatus.LIVE.value)
    )
    svc.repo.soft_delete_notice = AsyncMock(
        return_value=_notice_detail_row(status=NoticeStatus.DELETED.value)
    )

    await svc.delete_notice(
        project_id=PROJECT_ID,
        notice_id=NOTICE_ID,
    )
    svc.repo.soft_delete_notice.assert_awaited_once()


def _stub_detail_repo(svc: NoticesService) -> None:
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])


@pytest.mark.asyncio
async def test_get_notice_not_found():
    svc = _service()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await svc.get_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID)


@pytest.mark.asyncio
async def test_get_notice_success():
    svc = _service()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=_notice_detail_row())
    _stub_detail_repo(svc)
    detail = await svc.get_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID)
    assert detail.display_code == "NTC-1"


@pytest.mark.asyncio
async def test_update_notice_success():
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=_notice_detail_row())
    svc.repo.update_notice_fields = AsyncMock()
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()

    body = UpdateNoticeRequest(
        title="Updated title",
        publish_mode=NoticePublishMode.DRAFT,
    )
    detail = await svc.update_notice(
        project_id=PROJECT_ID,
        notice_id=NOTICE_ID,
        body=body,
    )
    assert detail.title == "Pool closure"
    svc.repo.update_notice_fields.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_notice_live_conflict():
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(
        return_value=_notice_detail_row(status=NoticeStatus.LIVE.value)
    )
    with pytest.raises(ConflictException):
        await svc.update_notice(
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            body=UpdateNoticeRequest(title="Updated"),
        )


@pytest.mark.asyncio
async def test_update_notice_schedule_publish_at():
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=_notice_detail_row())
    svc.repo.update_notice_fields = AsyncMock()
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    future = datetime.now(timezone.utc) + timedelta(days=2)
    body = UpdateNoticeRequest(publish_at=future)
    await svc.update_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID, body=body)
    fields = svc.repo.update_notice_fields.await_args.kwargs["fields"]
    assert fields["status"] == NoticeStatus.SCHEDULED.value


@pytest.mark.asyncio
async def test_delete_notice_already_deleted_returns_detail():
    svc = _service()
    deleted_row = _notice_detail_row(status=NoticeStatus.DELETED.value)
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=deleted_row)
    _stub_detail_repo(svc)
    detail = await svc.delete_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID)
    assert detail.status == NoticeStatus.DELETED


@pytest.mark.asyncio
async def test_restore_notice_success():
    svc = _service()
    deleted_row = _notice_detail_row(status=NoticeStatus.DELETED.value)
    svc.repo.fetch_notice_by_id = AsyncMock(
        side_effect=[deleted_row, _notice_detail_row(display_code="NTC-2")]
    )
    svc.repo.allocate_sequence_number = AsyncMock(return_value=2)
    svc.repo.insert_notice = AsyncMock(
        return_value=_notice_detail_row(display_code="NTC-2", id="new-id")
    )
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()

    detail = await svc.restore_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID)
    assert detail.display_code == "NTC-2"
    svc.repo.insert_notice.assert_awaited_once()


@pytest.mark.asyncio
async def test_restore_notice_not_deleted_conflict():
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=_notice_detail_row())
    with pytest.raises(ConflictException):
        await svc.restore_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID)


@pytest.mark.asyncio
async def test_duplicate_notice_success():
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(
        side_effect=[_notice_detail_row(), _notice_detail_row(display_code="NTC-2")]
    )
    svc.repo.allocate_sequence_number = AsyncMock(return_value=2)
    svc.repo.insert_notice = AsyncMock(
        return_value=_notice_detail_row(display_code="NTC-2", id="dup-id")
    )
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()

    detail = await svc.duplicate_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID)
    assert detail.display_code == "NTC-2"
    svc.repo.insert_notice.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_notice_live_conflict():
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(
        return_value=_notice_detail_row(status=NoticeStatus.LIVE.value)
    )
    with pytest.raises(ConflictException):
        await svc.duplicate_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID)


@pytest.mark.asyncio
async def test_pin_notice_success():
    from apps.user_service.app.schemas.notices import PinNoticeRequest

    svc = _service()
    live_row = _notice_detail_row(status=NoticeStatus.LIVE.value)
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=live_row)
    svc.repo.get_active_pin_for_notice = AsyncMock(return_value=None)
    svc.repo.get_active_pin_for_slot = AsyncMock(return_value=None)
    svc.repo.insert_pin = AsyncMock()
    _stub_detail_repo(svc)

    body = PinNoticeRequest(slot_index=1, pin_duration=NoticePinDuration.MANUAL)
    detail = await svc.pin_notice(
        project_id=PROJECT_ID,
        notice_id=NOTICE_ID,
        body=body,
    )
    assert detail.display_code == "NTC-1"
    svc.repo.insert_pin.assert_awaited_once()


@pytest.mark.asyncio
async def test_pin_notice_not_live_conflict():
    from apps.user_service.app.schemas.notices import PinNoticeRequest

    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=_notice_detail_row())
    svc.repo.get_active_pin_for_notice = AsyncMock(return_value=None)

    with pytest.raises(ConflictException):
        await svc.pin_notice(
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            body=PinNoticeRequest(slot_index=1),
        )


@pytest.mark.asyncio
async def test_pin_notice_slot_occupied_without_confirm():
    from apps.user_service.app.schemas.notices import PinNoticeRequest

    svc = _service()
    live_row = _notice_detail_row(status=NoticeStatus.LIVE.value)
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=live_row)
    svc.repo.get_active_pin_for_notice = AsyncMock(return_value=None)
    svc.repo.get_active_pin_for_slot = AsyncMock(
        return_value={
            "notice_id": "other-id",
            "display_code": "NTC-9",
            "title": "Other",
        }
    )

    with pytest.raises(ConflictException):
        await svc.pin_notice(
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            body=PinNoticeRequest(slot_index=1, confirm_pin_replace=False),
        )


@pytest.mark.asyncio
async def test_unpin_notice_success():
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=_notice_detail_row())
    svc.repo.deactivate_pins_for_notice = AsyncMock()
    _stub_detail_repo(svc)

    detail = await svc.unpin_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID)
    assert detail.display_code == "NTC-1"
    svc.repo.deactivate_pins_for_notice.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_reach_estimate():
    from apps.user_service.app.schemas.notices import ReachEstimateQuery

    svc = _service()
    svc.recipient_service = MagicMock()
    svc.recipient_service.estimate_reach = AsyncMock(return_value=(25, {"Owner": 25}))

    result = await svc.get_reach_estimate(
        project_id=PROJECT_ID,
        query=ReachEstimateQuery(
            groups="Owner",
            scope_type=NoticeScopeType.WHOLE_SOCIETY,
        ),
    )
    assert result.estimated_recipients == 25


@pytest.mark.asyncio
async def test_get_reach_estimate_towers_required():
    from apps.user_service.app.schemas.notices import ReachEstimateQuery

    svc = _service()
    with pytest.raises(ValidationException):
        await svc.get_reach_estimate(
            project_id=PROJECT_ID,
            query=ReachEstimateQuery(
                groups="Owner",
                scope_type=NoticeScopeType.BY_TOWER,
            ),
        )


@pytest.mark.asyncio
async def test_get_reach_estimate_rejects_deprecated_staff_manager_label():
    from apps.user_service.app.schemas.notices import ReachEstimateQuery

    svc = _service()
    with pytest.raises(ValidationException) as exc_info:
        await svc.get_reach_estimate(
            project_id=PROJECT_ID,
            query=ReachEstimateQuery(groups="Staff Manager"),
        )
    assert exc_info.value.message_key == "notices.errors.invalid_recipient_groups"


@pytest.mark.asyncio
async def test_get_reach_estimate_rejects_invalid_group_name():
    from apps.user_service.app.schemas.notices import ReachEstimateQuery

    svc = _service()
    with pytest.raises(ValidationException) as exc_info:
        await svc.get_reach_estimate(
            project_id=PROJECT_ID,
            query=ReachEstimateQuery(groups="owner"),
        )
    assert exc_info.value.message_key == "notices.errors.invalid_recipient_groups"


@pytest.mark.asyncio
async def test_get_reach_estimate_all_expands_to_every_group():
    """Success: reach estimate All expands to every persisted recipient group."""
    from apps.user_service.app.schemas.notices import ReachEstimateQuery

    svc = _service()
    svc.recipient_service = MagicMock()
    svc.recipient_service.estimate_reach = AsyncMock(
        return_value=(40, {"Owner": 10, "Tenant": 10, "Staff": 10, "Security": 10})
    )

    result = await svc.get_reach_estimate(
        project_id=PROJECT_ID,
        query=ReachEstimateQuery(groups="All"),
    )
    assert result.estimated_recipients == 40
    kwargs = svc.recipient_service.estimate_reach.await_args.kwargs
    assert kwargs["recipient_groups"] == ["Owner", "Tenant", "Staff", "Security"]


@pytest.mark.asyncio
async def test_get_reach_estimate_all_with_invalid_group_fails():
    """Failure: All plus an unknown group is still rejected."""
    from apps.user_service.app.schemas.notices import ReachEstimateQuery

    svc = _service()
    with pytest.raises(ValidationException) as exc_info:
        await svc.get_reach_estimate(
            project_id=PROJECT_ID,
            query=ReachEstimateQuery(groups="All,Everyone"),
        )
    assert exc_info.value.message_key == "notices.errors.invalid_recipient_groups"


@pytest.mark.asyncio
async def test_pin_notice_replaces_occupied_slot():
    from apps.user_service.app.schemas.notices import PinNoticeRequest

    svc = _service()
    live_row = _notice_detail_row(status=NoticeStatus.LIVE.value)
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=live_row)
    svc.repo.get_active_pin_for_notice = AsyncMock(return_value=None)
    svc.repo.get_active_pin_for_slot = AsyncMock(
        return_value={
            "notice_id": "other-id",
            "display_code": "NTC-9",
            "title": "Other",
        }
    )
    svc.repo.deactivate_pin_on_slot = AsyncMock()
    svc.repo.insert_pin = AsyncMock()
    _stub_detail_repo(svc)

    body = PinNoticeRequest(
        slot_index=1,
        pin_duration=NoticePinDuration.MANUAL,
        confirm_pin_replace=True,
    )
    await svc.pin_notice(project_id=PROJECT_ID, notice_id=NOTICE_ID, body=body)
    svc.repo.deactivate_pin_on_slot.assert_awaited_once()
    svc.repo.insert_pin.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_notice_live_with_pin():
    svc = _service()
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo.allocate_sequence_number = AsyncMock(return_value=3)
    svc.repo.insert_notice = AsyncMock(
        return_value=_notice_detail_row(status=NoticeStatus.LIVE.value)
    )
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()
    svc.repo.replace_attachments = AsyncMock()
    svc.repo.fetch_notice_by_id = AsyncMock(
        return_value=_notice_detail_row(status=NoticeStatus.LIVE.value)
    )
    svc.repo.get_active_pin_for_notice = AsyncMock(return_value=None)
    svc.repo.find_first_free_slot = AsyncMock(return_value=1)
    svc.repo.get_active_pin_for_slot = AsyncMock(return_value=None)
    svc.repo.insert_pin = AsyncMock()
    _stub_detail_repo(svc)
    svc.recipient_service = MagicMock()
    svc.recipient_service.resolve_recipient_user_ids = AsyncMock(return_value=[])
    svc.push_dispatcher = MagicMock()
    svc.push_dispatcher.send_to_user = AsyncMock()

    body = CreateNoticeRequest(
        title="Pool closure",
        description="Closed tomorrow",
        recipient_groups=[NoticeRecipientGroup.OWNER],
        publish_mode=NoticePublishMode.NOW,
        pin_to_banner=True,
        slot_index=1,
    )
    detail = await svc.create_notice(project_id=PROJECT_ID, body=body)
    assert detail.display_code == "NTC-1"
    svc.repo.insert_pin.assert_awaited_once()


def _stub_create_repo(svc: NoticesService, *, recipient_groups: list[str]) -> None:
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock()
    svc.repo.allocate_sequence_number = AsyncMock(return_value=4)
    svc.repo.insert_notice = AsyncMock(
        return_value=_notice_detail_row(status=NoticeStatus.LIVE.value)
    )
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_contact_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()
    svc.repo.replace_attachments = AsyncMock()
    svc.repo.list_recipient_groups = AsyncMock(return_value=recipient_groups)
    svc.repo.list_contact_recipient_ids = AsyncMock(return_value=[])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.fetch_notice_by_id = AsyncMock(
        return_value=_notice_detail_row(
            status=NoticeStatus.LIVE.value,
            recipient_groups=recipient_groups,
        )
    )
    svc.recipient_service = MagicMock()
    svc.recipient_service.filter_valid_project_contact_ids = AsyncMock(
        side_effect=lambda **_kwargs: _kwargs.get("contact_ids") or []
    )
    svc.recipient_service.resolve_recipient_user_ids = AsyncMock(return_value=[])
    svc.recipient_service.resolve_user_ids_for_contact_ids = AsyncMock(return_value=[])
    svc.push_dispatcher = MagicMock()
    svc.push_dispatcher.send_to_user = AsyncMock()


@pytest.mark.asyncio
async def test_create_notice_all_recipients_expands_to_every_group():
    """Success: All selects Owner, Tenant, Staff, and Security in one step."""
    all_groups = ["Owner", "Tenant", "Staff", "Security"]
    svc = _service()
    _stub_create_repo(svc, recipient_groups=all_groups)

    body = CreateNoticeRequest(
        title="Pool closure",
        description="Closed tomorrow",
        recipient_groups=[NoticeRecipientInputGroup.ALL],
        publish_mode=NoticePublishMode.NOW,
    )
    detail = await svc.create_notice(project_id=PROJECT_ID, body=body)
    assert detail.display_code == "NTC-1"
    assert svc.repo.replace_recipients.await_args.kwargs["recipient_groups"] == all_groups


@pytest.mark.asyncio
async def test_create_notice_lowercase_all_recipients_expands():
    """Success: lowercase all is accepted as the select-all shortcut."""
    all_groups = ["Owner", "Tenant", "Staff", "Security"]
    svc = _service()
    _stub_create_repo(svc, recipient_groups=all_groups)

    body = CreateNoticeRequest(
        title="Pool closure",
        recipient_groups=["all"],
        publish_mode=NoticePublishMode.NOW,
    )
    await svc.create_notice(project_id=PROJECT_ID, body=body)
    assert svc.repo.replace_recipients.await_args.kwargs["recipient_groups"] == all_groups


@pytest.mark.asyncio
async def test_create_notice_publish_now_with_contact_ids_only():
    """Success: publish now accepts direct contact targeting without groups."""
    contact_id = "55555555-5555-5555-5555-555555555555"
    svc = _service()
    _stub_create_repo(svc, recipient_groups=[])

    body = CreateNoticeRequest(
        title="Private reminder",
        contact_ids=[contact_id],
        publish_mode=NoticePublishMode.NOW,
    )
    detail = await svc.create_notice(project_id=PROJECT_ID, body=body)
    assert detail.display_code == "NTC-1"
    assert svc.repo.replace_contact_recipients.await_args.kwargs["contact_ids"] == [contact_id]


@pytest.mark.asyncio
async def test_create_notice_multiple_contact_ids_deduped():
    """Success: duplicate contact ids in the request are stored once."""
    contact_a = "55555555-5555-5555-5555-555555555555"
    contact_b = "66666666-6666-6666-6666-666666666666"
    svc = _service()
    _stub_create_repo(svc, recipient_groups=[])

    body = CreateNoticeRequest(
        title="Two residents",
        contact_ids=[contact_a, contact_b, contact_a, "  ", contact_b],
        publish_mode=NoticePublishMode.NOW,
    )
    await svc.create_notice(project_id=PROJECT_ID, body=body)
    assert svc.repo.replace_contact_recipients.await_args.kwargs["contact_ids"] == [
        contact_a,
        contact_b,
    ]


@pytest.mark.asyncio
async def test_create_notice_invalid_contact_id_fails():
    """Failure: contact ids not in the project are rejected before insert."""
    valid_id = "55555555-5555-5555-5555-555555555555"
    invalid_id = "77777777-7777-7777-7777-777777777777"
    svc = _service()
    svc.repo.insert_notice = AsyncMock()
    svc.recipient_service.filter_valid_project_contact_ids = AsyncMock(return_value=[valid_id])

    body = CreateNoticeRequest(
        title="Bad targeting",
        contact_ids=[valid_id, invalid_id],
        publish_mode=NoticePublishMode.NOW,
    )
    with pytest.raises(ValidationException) as exc_info:
        await svc.create_notice(project_id=PROJECT_ID, body=body)
    assert exc_info.value.message_key == "notices.errors.invalid_contact_recipients"
    assert invalid_id in exc_info.value.params["contact_ids"]
    svc.repo.insert_notice.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_notice_publish_now_with_contacts_dispatches_push():
    """Success: live notice push includes users resolved from contact_ids."""
    contact_id = "55555555-5555-5555-5555-555555555555"
    svc = _service()
    _stub_create_repo(svc, recipient_groups=[])
    svc.repo.list_contact_recipient_ids = AsyncMock(return_value=[contact_id])
    svc.recipient_service.resolve_user_ids_for_contact_ids = AsyncMock(return_value=["user-9"])

    body = CreateNoticeRequest(
        title="Direct notice",
        contact_ids=[contact_id],
        publish_mode=NoticePublishMode.NOW,
    )
    await svc.create_notice(project_id=PROJECT_ID, body=body)

    svc.recipient_service.resolve_user_ids_for_contact_ids.assert_awaited_once()
    svc.push_dispatcher.send_to_user.assert_awaited_once()
    assert svc.push_dispatcher.send_to_user.await_args.kwargs["recipient_user_id"] == "user-9"


def test_create_notice_schema_rejects_too_many_contact_ids():
    """Failure: schema rejects more than the configured contact id limit."""
    too_many = [
        f"{index:08x}-0000-4000-8000-000000000000"
        for index in range(NOTICE_MAX_CONTACT_RECIPIENTS + 1)
    ]
    with pytest.raises(ValidationError):
        CreateNoticeRequest(
            title="Too many contacts",
            contact_ids=too_many,
        )


@pytest.mark.asyncio
async def test_create_notice_publish_now_without_recipients_fails():
    """Failure: publish now still requires at least one recipient group."""
    svc = _service()
    body = CreateNoticeRequest(
        title="Pool closure",
        publish_mode=NoticePublishMode.NOW,
    )
    with pytest.raises(ValidationException) as exc_info:
        await svc.create_notice(project_id=PROJECT_ID, body=body)
    assert exc_info.value.message_key == "notices.errors.recipients_required"
    svc.repo.insert_notice.assert_not_called()


@pytest.mark.asyncio
async def test_create_notice_all_recipients_still_requires_towers_for_by_tower():
    """Failure: All recipients does not skip by-tower scope validation."""
    svc = _service()
    body = CreateNoticeRequest(
        title="Pool closure",
        recipient_groups=[NoticeRecipientInputGroup.ALL],
        scope_type=NoticeScopeType.BY_TOWER,
        tower_ids=[],
        publish_mode=NoticePublishMode.NOW,
    )
    with pytest.raises(ValidationException) as exc_info:
        await svc.create_notice(project_id=PROJECT_ID, body=body)
    assert exc_info.value.message_key == "notices.errors.towers_required"
    svc.repo.insert_notice.assert_not_called()


@pytest.mark.asyncio
async def test_update_notice_clears_contact_recipients_with_empty_list():
    """Success: PATCH contact_ids: [] removes all direct contact recipients."""
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=_notice_detail_row())
    svc.repo.update_notice_fields = AsyncMock()
    svc.repo.list_recipient_groups = AsyncMock(return_value=["Owner"])
    svc.repo.list_contact_recipient_ids = AsyncMock(return_value=["old-contact"])
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_contact_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()
    svc.repo.replace_attachments = AsyncMock()

    await svc.update_notice(
        project_id=PROJECT_ID,
        notice_id=NOTICE_ID,
        body=UpdateNoticeRequest(contact_ids=[]),
    )

    assert svc.repo.replace_contact_recipients.await_args.kwargs["contact_ids"] == []


@pytest.mark.asyncio
async def test_update_notice_all_recipients_expands_to_every_group():
    """Success: updating recipients to All expands to every persisted group."""
    all_groups = ["Owner", "Tenant", "Staff", "Security"]
    svc = _service()
    svc.repo.fetch_notice_by_id = AsyncMock(return_value=_notice_detail_row())
    svc.repo.update_notice_fields = AsyncMock()
    svc.repo.list_recipient_groups = AsyncMock(return_value=all_groups)
    svc.repo.list_towers_for_notice = AsyncMock(return_value=[])
    svc.repo.list_attachments = AsyncMock(return_value=[])
    svc.repo.replace_recipients = AsyncMock()
    svc.repo.replace_towers = AsyncMock()
    svc.repo.replace_attachments = AsyncMock()

    await svc.update_notice(
        project_id=PROJECT_ID,
        notice_id=NOTICE_ID,
        body=UpdateNoticeRequest(recipient_groups=[NoticeRecipientInputGroup.ALL]),
    )
    assert svc.repo.replace_recipients.await_args.kwargs["recipient_groups"] == all_groups
