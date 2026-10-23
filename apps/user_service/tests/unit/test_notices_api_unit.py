"""Unit tests for notices admin API route handlers."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.notices import (
    create_project_notice,
    delete_project_notice,
    duplicate_project_notice,
    get_project_notice,
    get_project_notice_reach_estimate,
    get_project_notice_summary,
    list_project_notices,
    pin_project_notice,
    publish_due_project_notices,
    restore_project_notice,
    unpin_project_notice,
    update_project_notice,
)
from apps.user_service.app.schemas.enums import (
    NoticeCategory,
    NoticePublishMode,
    NoticeScopeType,
    NoticeStatus,
)
from apps.user_service.app.schemas.notices import (
    CreateNoticeRequest,
    DeleteNoticeRequest,
    NoticeDetailResponse,
    NoticeListQuery,
    NoticeSummaryResponse,
    PinNoticeRequest,
    ReachEstimateResponse,
    UpdateNoticeRequest,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
NOTICE_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _skip_audit_logging():
    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_decorator._log_audit_event",
        new_callable=AsyncMock,
    ):
        yield


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/notices",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


def _user_context() -> UserContext:
    return UserContext(user_id="staff-1", email="staff@example.com", organization_id="org-1")


def _detail(**overrides) -> NoticeDetailResponse:
    now = datetime.now(timezone.utc)
    base = {
        "id": NOTICE_ID,
        "organization_id": "org-1",
        "project_id": PROJECT_ID,
        "display_code": "NTC-1",
        "status": NoticeStatus.DRAFT,
        "title": "Pool closure",
        "description": "Closed tomorrow",
        "category": NoticeCategory.MAINTENANCE,
        "category_label": "Maintenance",
        "recipient_groups": ["Owner"],
        "scope_type": NoticeScopeType.WHOLE_SOCIETY,
        "scope_label": "Whole society",
        "tower_ids": [],
        "tower_names": [],
        "attachments": [],
        "pinned": False,
        "editable": True,
        "view_count": 0,
        "like_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return NoticeDetailResponse(**base)


@pytest.mark.asyncio
@patch("apps.user_service.app.api.notices.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.notices.NoticesService")
async def test_notice_read_endpoints(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.get_summary = AsyncMock(
        return_value=NoticeSummaryResponse(
            all=1, live=1, scheduled=0, deleted=0, live_by_group={"Owner": 1}
        )
    )
    service.list_notices = AsyncMock(return_value=([], 0))
    service.get_reach_estimate = AsyncMock(
        return_value=ReachEstimateResponse(estimated_recipients=10, breakdown={"Owner": 10})
    )
    service.get_notice = AsyncMock(return_value=_detail())

    assert (
        await get_project_notice_summary(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await list_project_notices(
            request=_request(),
            project_id=PROJECT_ID,
            query=NoticeListQuery(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await get_project_notice_reach_estimate(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await get_project_notice(
            request=_request(),
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.notices.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.notices.NoticesService")
async def test_notice_write_endpoints(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    service = mock_service_cls.return_value
    service.create_notice = AsyncMock(return_value=_detail())
    service.update_notice = AsyncMock(return_value=_detail())
    service.delete_notice = AsyncMock(return_value=_detail(status=NoticeStatus.DELETED))
    service.restore_notice = AsyncMock(return_value=_detail())
    service.duplicate_notice = AsyncMock(return_value=_detail(display_code="NTC-2"))
    service.pin_notice = AsyncMock(return_value=_detail(pinned=True))
    service.unpin_notice = AsyncMock(return_value=_detail())
    service.publish_due_notices = AsyncMock(return_value=[NOTICE_ID])

    assert (
        await create_project_notice(
            request=_request(),
            project_id=PROJECT_ID,
            body=CreateNoticeRequest(title="Hello", publish_mode=NoticePublishMode.DRAFT),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201
    assert (
        await update_project_notice(
            request=_request(),
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            body=UpdateNoticeRequest(title="Updated"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await delete_project_notice(
            request=_request(),
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            body=DeleteNoticeRequest(reason="old"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await restore_project_notice(
            request=_request(),
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201
    assert (
        await duplicate_project_notice(
            request=_request(),
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 201
    assert (
        await pin_project_notice(
            request=_request(),
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            body=PinNoticeRequest(slot_index=1),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await unpin_project_notice(
            request=_request(),
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200
    assert (
        await publish_due_project_notices(
            request=_request(),
            project_id=PROJECT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch("apps.user_service.app.api.notices.ensure_staff_project_access", new_callable=AsyncMock)
@patch("apps.user_service.app.api.notices.NoticesService")
async def test_get_notice_not_found(mock_service_cls, mock_access):
    mock_access.return_value = _user_context()
    mock_service_cls.return_value.get_notice = AsyncMock(
        side_effect=NotFoundException(message_key="notices.errors.not_found", custom_code=404)
    )
    with pytest.raises(NotFoundException):
        await get_project_notice(
            request=_request(),
            project_id=PROJECT_ID,
            notice_id=NOTICE_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )
