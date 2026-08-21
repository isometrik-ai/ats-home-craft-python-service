"""Unit tests for resident notices API route handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.notices_resident import (
    get_resident_notice,
    get_resident_notice_banner,
    like_resident_notice,
    list_resident_notices,
    unlike_resident_notice,
)
from apps.user_service.app.schemas.notices import (
    ResidentBannerQuery,
    ResidentNoticeListQuery,
)
from apps.user_service.app.utils.common_utils import UserContext

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
NOTICE_ID = "22222222-2222-2222-2222-222222222222"


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


def _viewer():
    viewer = MagicMock()
    viewer.contact_id = "contact-1"
    viewer.contact_user_id = "user-1"
    viewer.user_context = UserContext(
        user_id="user-1",
        email="resident@example.com",
        organization_id="org-1",
    )
    return viewer


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.notices_resident.extract_notice_viewer_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.notices_resident.NoticesResidentService")
async def test_resident_notice_read_endpoints(mock_service_cls, mock_viewer_ctx):
    mock_viewer_ctx.return_value = _viewer()
    service = mock_service_cls.return_value
    service.get_banner = AsyncMock(return_value=[])
    service.list_notices = AsyncMock(return_value=([], 0))
    service.get_notice = AsyncMock(return_value=MagicMock(model_dump=lambda **_: {"id": NOTICE_ID}))

    db = MagicMock()
    user = {"sub": "user-1"}
    assert (
        await get_resident_notice_banner(
            request=_request(),
            query=ResidentBannerQuery(project_id=PROJECT_ID),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await list_resident_notices(
            request=_request(),
            query=ResidentNoticeListQuery(project_id=PROJECT_ID),
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await get_resident_notice(
            request=_request(),
            notice_id=NOTICE_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.notices_resident.extract_notice_viewer_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.notices_resident.NoticesResidentService")
async def test_resident_notice_like_endpoints(mock_service_cls, mock_viewer_ctx):
    mock_viewer_ctx.return_value = _viewer()
    service = mock_service_cls.return_value
    service.like_notice = AsyncMock(return_value=MagicMock(model_dump=lambda **_: {"liked": True}))
    service.unlike_notice = AsyncMock(
        return_value=MagicMock(model_dump=lambda **_: {"liked": False})
    )

    db = MagicMock()
    user = {"sub": "user-1"}
    assert (
        await like_resident_notice(
            request=_request(),
            notice_id=NOTICE_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
    assert (
        await unlike_resident_notice(
            request=_request(),
            notice_id=NOTICE_ID,
            db_connection=db,
            current_user=user,
        )
    ).status_code == 200
