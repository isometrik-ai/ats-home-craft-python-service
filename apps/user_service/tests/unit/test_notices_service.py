"""Unit tests for NoticesService validation helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import (
    NoticePinDuration,
    NoticePublishMode,
    NoticeScopeType,
    NoticeStatus,
)
from apps.user_service.app.schemas.notices import NoticeListQuery
from apps.user_service.app.services.notices_service import NoticesService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ConflictException, ValidationException

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
            scope_type=NoticeScopeType.WHOLE_SOCIETY,
            tower_ids=[],
        )


def test_validate_publish_requirements_missing_towers():
    with pytest.raises(ValidationException):
        NoticesService._validate_publish_requirements(
            None,
            recipient_groups=["Owner"],
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
