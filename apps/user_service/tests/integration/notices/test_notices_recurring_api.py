"""Integration tests for recurring notice API routes (success + failure)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.user_service.app.schemas.enums import (
    NoticeCategory,
    NoticePublishMode,
    NoticeStatus,
)
from apps.user_service.app.schemas.notices import (
    NoticeDetailResponse,
    NoticeRecurrencePreviewResponse,
)
from apps.user_service.tests.integration.helpers import (
    patch_ensure_staff_project_access,
)
from apps.user_service.tests.utils.assertions import assert_success
from libs.shared_utils.http_exceptions import ConflictException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_API = "apps.user_service.app.api.notices"
_SERVICE = "apps.user_service.app.services.notices_service.NoticesService"

PROJECT_ID = "990e8400-e29b-41d4-a716-446655440004"
NOTICE_ID = "770e8400-e29b-41d4-a716-446655440001"
ORG = "org-123"

_REPEAT_WEEKLY = {
    "frequency": "weekly",
    "weekly_days": [1, 3, 5],
    "time_of_day": "09:00:00",
    "starts_on": "2026-10-22",
    "end_type": "never",
}


def _patch_admin_access(monkeypatch) -> None:
    patch_ensure_staff_project_access(monkeypatch, _API, org_id=ORG)


def _fake_detail(**overrides) -> NoticeDetailResponse:
    data = {
        "id": NOTICE_ID,
        "organization_id": ORG,
        "project_id": PROJECT_ID,
        "display_code": "NTC-1",
        "status": NoticeStatus.DRAFT,
        "title": "Recurring notice",
        "description": "Body",
        "category": NoticeCategory.GENERAL,
        "category_label": "General",
        "recipient_groups": ["Owner"],
        "scope_type": "whole_society",
        "scope_label": "Whole society",
        "editable": True,
        "is_recurring_template": True,
        "series_status": "active",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    data.update(overrides)
    return NoticeDetailResponse(**data)


@pytest.mark.asyncio
async def test_recurrence_preview_success(monkeypatch, client):
    """POST recurrence-preview returns upcoming sends."""
    _patch_admin_access(monkeypatch)
    first = datetime(2026, 10, 23, 3, 30, tzinfo=timezone.utc)

    async def fake_preview(_self, *, body):
        del _self
        assert body.repeat.frequency.value == "weekly"
        return NoticeRecurrencePreviewResponse(
            recurrence_summary="Every Monday, Wednesday and Friday at 9:00 AM.",
            first_send_at=first,
            upcoming_sends=[first],
        )

    monkeypatch.setattr(f"{_SERVICE}.preview_recurrence", fake_preview)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/notices/recurrence-preview",
        json={"repeat": _REPEAT_WEEKLY},
    )
    body = assert_success(res, 200)
    assert body["data"]["first_send_at"] is not None
    assert "recurrence_summary" in body["data"]


@pytest.mark.asyncio
async def test_recurrence_preview_validation_failure(monkeypatch, client):
    """POST recurrence-preview returns 422 when repeat rule is invalid."""
    _patch_admin_access(monkeypatch)

    async def fake_preview(_self, *, body):
        del _self, body
        raise ValidationException(
            message_key="notices.errors.weekly_days_required",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    monkeypatch.setattr(f"{_SERVICE}.preview_recurrence", fake_preview)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/notices/recurrence-preview",
        json={
            "repeat": {
                "frequency": "weekly",
                "weekly_days": [],
                "time_of_day": "09:00:00",
                "starts_on": "2026-10-22",
                "end_type": "never",
            }
        },
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_create_recurring_series_start_success(monkeypatch, client):
    """POST create with repeat + series_start returns template detail."""
    _patch_admin_access(monkeypatch)

    async def fake_create(_self, *, project_id: str, body):
        del _self
        assert project_id == PROJECT_ID
        assert body.publish_mode == NoticePublishMode.SERIES_START
        assert body.repeat is not None
        return _fake_detail()

    monkeypatch.setattr(f"{_SERVICE}.create_notice", fake_create)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/notices",
        json={
            "title": "Pool cleaning",
            "description": "Weekly reminder",
            "recipient_groups": ["Owner"],
            "publish_mode": "series_start",
            "repeat": _REPEAT_WEEKLY,
        },
    )
    body = assert_success(res, 201)
    assert body["data"]["is_recurring_template"] is True


@pytest.mark.asyncio
async def test_create_recurring_with_publish_now_fails(monkeypatch, client):
    """POST create returns 422 when service rejects repeat with publish now."""
    _patch_admin_access(monkeypatch)

    async def fake_create(_self, *, project_id: str, body):
        del _self, project_id
        if body.repeat is not None and body.publish_mode == NoticePublishMode.NOW:
            raise ValidationException(
                message_key="notices.errors.repeat_with_schedule",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return _fake_detail()

    monkeypatch.setattr(f"{_SERVICE}.create_notice", fake_create)

    res = await client.post(
        f"/v1/projects/{PROJECT_ID}/notices",
        json={
            "title": "Pool cleaning",
            "recipient_groups": ["Owner"],
            "publish_mode": "now",
            "repeat": _REPEAT_WEEKLY,
        },
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_patch_recurring_active_edit_conflict(monkeypatch, client):
    """PATCH on active recurring template returns 409."""
    _patch_admin_access(monkeypatch)

    async def fake_update(_self, *, project_id: str, notice_id: str, body):
        del _self, project_id, notice_id, body
        raise ConflictException(
            message_key="notices.errors.series_not_editable",
            custom_code=CustomStatusCode.CONFLICT,
        )

    monkeypatch.setattr(f"{_SERVICE}.update_notice", fake_update)

    res = await client.patch(
        f"/v1/projects/{PROJECT_ID}/notices/{NOTICE_ID}",
        json={"title": "Changed"},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_patch_series_pause_success(monkeypatch, client):
    """PATCH series_action pause returns updated template."""
    _patch_admin_access(monkeypatch)

    async def fake_update(_self, *, project_id: str, notice_id: str, body):
        del _self
        assert project_id == PROJECT_ID
        assert notice_id == NOTICE_ID
        assert body.series_action is not None
        assert body.series_action.value == "pause"
        return _fake_detail(series_status="paused", editable=True)

    monkeypatch.setattr(f"{_SERVICE}.update_notice", fake_update)

    res = await client.patch(
        f"/v1/projects/{PROJECT_ID}/notices/{NOTICE_ID}",
        json={"series_action": "pause"},
    )
    body = assert_success(res, 200)
    assert body["data"]["series_status"] == "paused"
