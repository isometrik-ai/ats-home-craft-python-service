"""Integration tests for admin /v1/projects/{project_id}/notices endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.app.schemas.enums import (
    NoticeCategory,
    NoticePublishMode,
    NoticeScopeType,
    NoticeStatus,
)
from apps.user_service.app.schemas.notices import (
    NoticeDetailResponse,
    NoticeListItemResponse,
    NoticeSummaryResponse,
)
from apps.user_service.tests.integration.helpers import (
    patch_ensure_staff_project_access,
)
from apps.user_service.tests.utils.assertions import assert_success
from libs.shared_utils.http_exceptions import ConflictException, NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode

_API = "apps.user_service.app.api.notices"
_SERVICE = "apps.user_service.app.services.notices_service.NoticesService"

PROJECT_ID = "990e8400-e29b-41d4-a716-446655440004"
NOTICE_ID = "770e8400-e29b-41d4-a716-446655440001"
ORG = "org-123"


def _patch_admin_access(monkeypatch) -> None:
    patch_ensure_staff_project_access(monkeypatch, _API, org_id=ORG)


def _fake_detail(**overrides) -> NoticeDetailResponse:
    data = {
        "id": NOTICE_ID,
        "organization_id": ORG,
        "project_id": PROJECT_ID,
        "display_code": "NTC-1",
        "status": NoticeStatus.DRAFT,
        "title": "Test notice",
        "description": "Body",
        "category": NoticeCategory.GENERAL,
        "category_label": "General",
        "recipient_groups": ["Owner"],
        "scope_type": NoticeScopeType.WHOLE_SOCIETY,
        "scope_label": "Whole society",
        "editable": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    data.update(overrides)
    return NoticeDetailResponse(**data)


@pytest.mark.asyncio
async def test_get_project_notice_summary(monkeypatch, client):
    """GET summary returns tab counts."""
    _patch_admin_access(monkeypatch)

    async def fake_get_summary(_self, *, project_id: str):
        del _self
        assert project_id == PROJECT_ID
        return NoticeSummaryResponse(
            all=3,
            live=1,
            scheduled=1,
            deleted=1,
            live_by_group={"Owner": 1},
        )

    monkeypatch.setattr(f"{_SERVICE}.get_summary", fake_get_summary)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/notices/summary")
    body = assert_success(res, 200)
    assert body["data"]["live"] == 1


@pytest.mark.asyncio
async def test_list_project_notices(monkeypatch, client):
    """GET list returns paginated notices."""
    _patch_admin_access(monkeypatch)

    async def fake_list_notices(_self, *, project_id: str, query):
        del _self
        assert project_id == PROJECT_ID
        return [
            NoticeListItemResponse(
                id=NOTICE_ID,
                display_code="NTC-1",
                status=NoticeStatus.LIVE,
                title="Live notice",
                description="Body",
                category=NoticeCategory.GENERAL,
                category_label="General",
                recipient_groups=["Owner"],
                scope_type=NoticeScopeType.WHOLE_SOCIETY,
                editable=False,
                created_at=datetime.now(timezone.utc),
            )
        ], 1

    monkeypatch.setattr(f"{_SERVICE}.list_notices", fake_list_notices)

    res = await client.get(
        f"/v1/projects/{PROJECT_ID}/notices",
        params={"status": "live"},
    )
    body = assert_success(res, 200)
    assert body["data"][0]["display_code"] == "NTC-1"


@pytest.mark.asyncio
async def test_get_project_notice(monkeypatch, client):
    """GET detail returns one notice."""
    _patch_admin_access(monkeypatch)

    async def fake_get_notice(_self, *, project_id: str, notice_id: str):
        del _self
        assert project_id == PROJECT_ID
        assert notice_id == NOTICE_ID
        return _fake_detail()

    monkeypatch.setattr(f"{_SERVICE}.get_notice", fake_get_notice)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/notices/{NOTICE_ID}")
    body = assert_success(res, 200)
    assert body["data"]["id"] == NOTICE_ID


@pytest.mark.asyncio
async def test_create_project_notice(monkeypatch, client):
    """POST create returns new notice."""
    _patch_admin_access(monkeypatch)

    async def fake_create_notice(_self, *, project_id: str, body):
        del _self
        assert project_id == PROJECT_ID
        assert body.publish_mode == NoticePublishMode.DRAFT
        return _fake_detail()

    monkeypatch.setattr(f"{_SERVICE}.create_notice", fake_create_notice)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/notices",
        json={
            "title": "Draft notice",
            "description": "Body",
            "publish_mode": "draft",
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["display_code"] == "NTC-1"


@pytest.mark.asyncio
async def test_update_live_notice_conflict(monkeypatch, client):
    """PATCH live notice returns 409."""
    _patch_admin_access(monkeypatch)

    async def fake_update_notice(_self, *, project_id: str, notice_id: str, body):
        del _self, project_id, notice_id, body
        raise ConflictException(
            message_key="notices.errors.not_editable",
            custom_code=CustomStatusCode.CONFLICT,
        )

    monkeypatch.setattr(f"{_SERVICE}.update_notice", fake_update_notice)

    res = await client.patch(
        f"/v1/projects/{PROJECT_ID}/notices/{NOTICE_ID}",
        json={"title": "Updated"},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_duplicate_live_notice_conflict(monkeypatch, client):
    """POST duplicate on live notice returns 409."""
    _patch_admin_access(monkeypatch)

    async def fake_duplicate_notice(_self, *, project_id: str, notice_id: str):
        del _self, project_id, notice_id
        raise ConflictException(
            message_key="notices.errors.duplicate_live_forbidden",
            custom_code=CustomStatusCode.CONFLICT,
        )

    monkeypatch.setattr(f"{_SERVICE}.duplicate_notice", fake_duplicate_notice)

    res = await client.post(f"/v1/projects/{PROJECT_ID}/notices/{NOTICE_ID}/duplicate")
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_restore_project_notice(monkeypatch, client):
    """POST restore returns draft notice."""
    _patch_admin_access(monkeypatch)

    async def fake_restore_notice(_self, *, project_id: str, notice_id: str):
        del _self, project_id, notice_id
        return _fake_detail(status=NoticeStatus.DRAFT)

    monkeypatch.setattr(f"{_SERVICE}.restore_notice", fake_restore_notice)

    res = await client.post(f"/v1/projects/{PROJECT_ID}/notices/{NOTICE_ID}/restore")
    body = assert_success(res, 201)
    assert body["data"]["status"] == "draft"


@pytest.mark.asyncio
async def test_get_notice_not_found(monkeypatch, client):
    """GET detail returns 404 when missing."""
    _patch_admin_access(monkeypatch)

    async def fake_get_notice(_self, *, project_id: str, notice_id: str):
        del _self, project_id, notice_id
        raise NotFoundException(
            message_key="notices.errors.not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    monkeypatch.setattr(f"{_SERVICE}.get_notice", fake_get_notice)

    res = await client.get(f"/v1/projects/{PROJECT_ID}/notices/{NOTICE_ID}")
    assert res.status_code == 404
